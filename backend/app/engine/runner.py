"""
Engine main loop: orchestrates signal_tick and exec_tick.

Signal Tick (at SIGNAL_TF boundary):
  1. Universe refresh
  2. Datafeed collection
  3. TrendScore computation
  4. Sizing → target_qty
  5. Risk guard check

Exec Tick (every execution_tick_sec):
  1. Current positions fetch
  2. Delta = target − current
  3. ExecutionEngine.tick()
  4. Bracket management
  5. Accounting
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.engine.accounting import ledger
from app.engine.config_model import EngineConfig, load_engine_config, save_engine_config
from app.engine.datafeed import fetch_closes, fetch_funding, fetch_vol_map
from app.engine.execution import ExecutionEngine
from app.engine.risk_guard import run_all_checks
from app.engine.signals import (
    SignalSnapshot,
    apply_funding_overlay,
    apply_rsi_overlay,
    compute_trend_scores,
)
from app.engine.sizing import SizingResult, compute_target_positions
from app.engine.universe import UniverseResult, get_universe
from app.engine.ws_stream import UserDataStream
from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)


class EngineRunner:
    """Main engine loop managing signal and execution ticks."""

    def __init__(self) -> None:
        self._config: EngineConfig = EngineConfig()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Sub-components
        self._execution = ExecutionEngine()
        self._ws_stream = UserDataStream()

        # State
        self._universe: Optional[UniverseResult] = None
        self._last_signal_tick: float = 0.0
        self._last_exec_tick: float = 0.0
        self._signal_count: int = 0
        self._exec_count: int = 0
        self._snapshots: dict[str, SignalSnapshot] = {}
        self._sizing_result: Optional[SizingResult] = None
        self._equity: float = 0.0
        self._peak_equity: float = 0.0
        self._current_symbols: set[str] = set()
        self._status_reason: str = ""
        self._risk_warnings: list[str] = []

    # ── Public API ───────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def config(self) -> EngineConfig:
        return self._config

    @config.setter
    def config(self, cfg: EngineConfig) -> None:
        self._config = cfg

    def get_status(self) -> dict[str, Any]:
        active_symbols = list(self._sizing_result.targets.keys()) if self._sizing_result else []
        return {
            "running": self._running,
            "reason": self._status_reason,
            "profile": self._config.profile,
            "symbol": self._config.symbol,
            "active_symbols": active_symbols,
            "equity": round(self._equity, 2),
            "peak_equity": round(self._peak_equity, 2),
            "gross_exposure": round(
                self._sizing_result.gross_notional if self._sizing_result else 0.0, 2
            ),
            "universe_size": len(self._universe.symbols) if self._universe else 0,
        }

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Return per-symbol target/current/weight/TrendScore for UI."""
        if not self._sizing_result:
            return []
        result = []
        for sym, tp in self._sizing_result.targets.items():
            snap = self._snapshots.get(sym)
            result.append({
                "symbol": sym,
                "side": tp.side,
                "target_qty": tp.target_qty,
                "weight": round(tp.weight, 4),
                "trend_score": round(tp.trend_score, 4),
                "target_notional": round(tp.target_notional, 2),
                "rsi": round(snap.rsi, 1) if snap else None,
                "funding_rate": round(snap.funding_rate, 6) if snap else None,
            })
        return result

    def get_signals(self) -> list[dict[str, Any]]:
        """Return full universe TrendScore snapshot."""
        result = []
        for sym, snap in self._snapshots.items():
            result.append({
                "symbol": sym,
                "trend_score_raw": round(snap.trend_score_raw, 4),
                "trend_score": round(snap.trend_score, 4),
                "final_score": round(snap.final_score, 4),
                "rsi": round(snap.rsi, 1),
                "rsi_scale": round(snap.rsi_scale, 3),
                "funding_rate": round(snap.funding_rate, 6),
                "funding_scale": round(snap.funding_scale, 3),
                "horizons": snap.horizon_signals,
            })
        return result

    def get_insight(self) -> dict[str, Any]:
        """Return comprehensive engine insight data for Insight tab."""
        # Engine pulse
        now = time.time()
        signal_interval = self._signal_interval_sec()
        exec_interval = self._config.execution_tick_sec
        
        time_since_signal = now - self._last_signal_tick if self._last_signal_tick > 0 else 0
        time_since_exec = now - self._last_exec_tick if self._last_exec_tick > 0 else 0
        next_signal = max(0, signal_interval - time_since_signal)
        next_exec = max(0, exec_interval - time_since_exec)
        
        engine_pulse = {
            "last_signal_tick": self._last_signal_tick,
            "last_exec_tick": self._last_exec_tick,
            "signal_count": self._signal_count,
            "exec_count": self._exec_count,
            "signal_interval_sec": signal_interval,
            "exec_interval_sec": exec_interval,
            "time_since_signal_sec": round(time_since_signal, 1),
            "time_since_exec_sec": round(time_since_exec, 1),
            "next_signal_sec": round(next_signal, 1),
            "next_exec_sec": round(next_exec, 1),
            "signal_tf": self._config.signal_tf,
        }
        
        # Market summary
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        total_score = 0.0
        
        for snap in self._snapshots.values():
            total_score += snap.final_score
            if snap.final_score > 0.1:
                bullish_count += 1
            elif snap.final_score < -0.1:
                bearish_count += 1
            else:
                neutral_count += 1
        
        avg_score = total_score / len(self._snapshots) if self._snapshots else 0.0
        
        # Market temperature label
        if avg_score >= 0.3:
            temperature = "강한 상승"
        elif avg_score >= 0.1:
            temperature = "약한 상승"
        elif avg_score <= -0.3:
            temperature = "강한 하락"
        elif avg_score <= -0.1:
            temperature = "약한 하락"
        else:
            temperature = "중립"
        
        market_summary = {
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "avg_trend_score": round(avg_score, 4),
            "temperature": temperature,
        }
        
        # Risk status
        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - self._equity) / self._peak_equity
        
        gross_leverage = 0.0
        if self._equity > 0 and self._sizing_result:
            gross_leverage = self._sizing_result.gross_notional / self._equity
        
        risk_status = {
            "equity": round(self._equity, 2),
            "peak_equity": round(self._peak_equity, 2),
            "drawdown_pct": round(drawdown_pct, 4),
            "drawdown_threshold": self._config.drawdown_kill_pct,
            "gross_leverage": round(gross_leverage, 2),
            "max_leverage": self._config.effective_leverage_target,
            "warnings": self._risk_warnings.copy(),
            "kill_active": not self._running and "kill" in self._status_reason.lower(),
        }
        
        # Universe scan
        universe_scan = {
            "selected_count": len(self._universe.symbols) if self._universe else 0,
            "excluded": self._universe.excluded if self._universe else [],
            "total_scanned": (
                len(self._universe.symbols) + len(self._universe.excluded)
                if self._universe
                else 0
            ),
        }
        
        return {
            "engine_pulse": engine_pulse,
            "market_summary": market_summary,
            "risk_status": risk_status,
            "universe_scan": universe_scan,
        }

    def start(self) -> dict[str, Any]:
        if self._running:
            return {"ok": False, "message": "Already running"}
        if not binance_client.is_configured():
            return {"ok": False, "message": "Binance API not configured"}

        self._config = load_engine_config()
        self._running = True
        self._stop_event.clear()
        self._status_reason = ""

        # Fetch initial equity
        try:
            acct = binance_client.account()
            self._equity = float(acct.get("totalWalletBalance", 0) or 0)
            self._peak_equity = self._equity
        except Exception as exc:
            logger.warning("runner: initial equity fetch failed: %s", exc)

        # Start WebSocket stream
        self._ws_stream.start(
            on_fill=self._on_ws_fill,
            on_account_update=self._on_ws_account,
        )

        # Start main loop thread
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()

        ledger.record("engine_start", {"profile": self._config.profile, "equity": self._equity})
        logger.info("Engine started (profile=%s, equity=%.2f)", self._config.profile, self._equity)
        return {"ok": True, "message": "Engine started"}

    def stop(self) -> dict[str, Any]:
        if not self._running:
            return {"ok": False, "message": "Not running"}
        self._running = False
        self._stop_event.set()
        self._ws_stream.stop()
        self._execution.reset()
        self._status_reason = "Stopped by user"
        ledger.record("engine_stop", {"reason": "user"})
        logger.info("Engine stopped by user")
        return {"ok": True, "message": "Engine stopped"}

    # ── Main loop ────────────────────────────────────────────────

    def _main_loop(self) -> None:
        logger.info("Engine main loop started")

        # Do an immediate signal tick
        self._signal_tick()

        while not self._stop_event.is_set():
            now = time.time()

            # Signal tick: at TF boundary (approximate with timer)
            signal_interval = self._signal_interval_sec()
            if (now - self._last_signal_tick) >= signal_interval:
                self._signal_tick()

            # Exec tick
            exec_interval = self._config.execution_tick_sec
            if (now - self._last_exec_tick) >= exec_interval:
                self._exec_tick()

            # Sleep 1 second
            self._stop_event.wait(1.0)

        logger.info("Engine main loop exited")

    def _signal_interval_sec(self) -> float:
        """Approximate signal tick interval in seconds."""
        tf = self._config.signal_tf
        if tf == "1d":
            return 4 * 3600  # re-evaluate every 4 hours
        elif tf == "4h":
            return 4 * 3600
        elif tf == "1h":
            return 3600
        return 3600

    # ── Signal tick ──────────────────────────────────────────────

    def _signal_tick(self) -> None:
        self._last_signal_tick = time.time()
        self._signal_count += 1
        cfg = self._config

        try:
            # 1. Universe refresh
            self._universe = get_universe(
                top_n=cfg.universe_top_n,
                listing_age_days=cfg.listing_age_days,
                max_spread_pct=cfg.max_spread_pct,
            )
            symbols = self._universe.symbol_names
            if not symbols:
                logger.warning("signal_tick: empty universe")
                return

            # 2. Data collection
            max_horizon = max(cfg.horizons) if cfg.horizons else 365
            closes_map = fetch_closes(symbols, cfg.signal_tf, max_horizon)
            if not closes_map:
                logger.warning("signal_tick: no close data")
                return

            # 3. TrendScore
            self._snapshots = compute_trend_scores(
                closes_map, cfg.horizons, cfg.signal_tf, cfg.deadzone_threshold,
            )

            # RSI overlay
            apply_rsi_overlay(
                self._snapshots, closes_map,
                rsi_period=cfg.rsi_period,
                rsi_overbought=cfg.rsi_overbought,
                rsi_oversold=cfg.rsi_oversold,
                scale_overbought=cfg.rsi_scale_overbought,
                scale_oversold=cfg.rsi_scale_oversold,
            )

            # Funding overlay
            if cfg.funding_scale_enabled:
                funding_map = fetch_funding(symbols)
                apply_funding_overlay(self._snapshots, funding_map)

            # 4. Vol map + sizing
            vol_map = fetch_vol_map(symbols, cfg.signal_tf, cfg.vol_window)
            penalty_map = self._universe.penalty_map()

            # Update equity
            try:
                acct = binance_client.account()
                self._equity = float(acct.get("totalWalletBalance", 0) or 0)
                if self._equity > self._peak_equity:
                    self._peak_equity = self._equity
            except Exception:
                pass

            self._sizing_result = compute_target_positions(
                self._snapshots, vol_map, penalty_map,
                equity=self._equity,
                target_vol=cfg.target_portfolio_vol,
                leverage_target=cfg.effective_leverage_target,
                top_k_enabled=cfg.top_k_enabled,
                top_k=cfg.top_k,
                replace_threshold=cfg.replace_threshold,
                min_weight_floor=cfg.min_weight_floor,
                max_weight_cap=cfg.max_weight_cap,
                current_symbols=self._current_symbols,
            )

            # 5. Risk guard
            positions = []
            try:
                raw_positions = binance_client.position_information()
                positions = [p for p in raw_positions if float(p.get("positionAmt", 0)) != 0]
            except Exception:
                pass

            risk = run_all_checks(
                current_equity=self._equity,
                peak_equity=self._peak_equity,
                positions=positions,
                kill_pct=cfg.drawdown_kill_pct,
                max_gross_leverage=cfg.effective_leverage_target * 2,
            )
            if risk.kill:
                self._running = False
                self._stop_event.set()
                self._status_reason = f"Kill switch: {risk.reason}"
                ledger.record("risk_kill", {"reason": risk.reason})
                logger.error("RISK KILL: %s", risk.reason)
                return
            if risk.warnings:
                self._risk_warnings = risk.warnings.copy()
                for w in risk.warnings:
                    logger.warning("Risk warning: %s", w)
            else:
                self._risk_warnings = []

            # Record signal snapshot
            signal_data = {
                sym: {
                    "trend_score": round(s.trend_score, 4),
                    "final_score": round(s.final_score, 4),
                }
                for sym, s in self._snapshots.items()
            }
            ledger.record_signal_snapshot(signal_data)
            ledger.record_equity_snapshot(self._equity)

            # Update current symbols
            if self._sizing_result:
                self._current_symbols = set(self._sizing_result.targets.keys())

            logger.info(
                "signal_tick: %d symbols, equity=%.2f, targets=%d",
                len(symbols), self._equity,
                len(self._sizing_result.targets) if self._sizing_result else 0,
            )

        except Exception as exc:
            logger.exception("signal_tick error: %s", exc)

    # ── Exec tick ────────────────────────────────────────────────

    def _exec_tick(self) -> None:
        self._last_exec_tick = time.time()
        self._exec_count += 1

        if not self._sizing_result or not self._sizing_result.targets:
            ledger.record(
                "exec_tick_skip",
                {
                    "reason": "no_targets",
                    "message": "Sizing produced no target positions (deadzone/top-k/min_weight).",
                },
            )
            logger.info("exec_tick: no targets, skip")
            return

        try:
            # Set Binance account leverage for margin efficiency.
            # Note: Binance leverage ≠ actual exposure. It only determines margin requirement.
            # Actual risk is controlled by position sizing (effective_leverage_target).
            # We set Binance leverage high enough to avoid "insufficient margin" errors.
            cfg = self._config
            max_lev = max(10, min(20, int(cfg.effective_leverage_target * 2)))
            for sym in self._sizing_result.targets:
                try:
                    binance_client.set_leverage(symbol=sym, leverage=max_lev)
                except Exception:
                    pass

            # Execute and get summary for activity log
            summary = self._execution.tick(
                targets=self._sizing_result.targets,
                config=cfg,
                equity=self._equity,
            )
            if summary is not None:
                ledger.record("exec_tick_summary", summary)

        except Exception as exc:
            logger.exception("exec_tick error: %s", exc)

    # ── WS callbacks ─────────────────────────────────────────────

    def _on_ws_fill(self, fill: dict[str, Any]) -> None:
        """Handle fill from WebSocket."""
        ledger.record_fill(
            symbol=fill.get("symbol", ""),
            side=fill.get("side", ""),
            qty=fill.get("filled_qty", 0),
            price=fill.get("avg_price", 0),
            realized_pnl=fill.get("realized_pnl", 0),
        )

    def _on_ws_account(self, data: dict[str, Any]) -> None:
        """Handle account update from WebSocket."""
        equity = self._ws_stream.equity
        if equity > 0:
            self._equity = equity
            if equity > self._peak_equity:
                self._peak_equity = equity


# Module-level singleton
engine = EngineRunner()
