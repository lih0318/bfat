"""
Engine main loop: orchestrates signal_tick and exec_tick.

Rollout Phases (Isolated 전환):
  Phase A (✅ 현재): ISOLATED 강제 + risk_per_trade_pct 기반 사이징 + 심볼별 레버리지
  Phase B: risk_per_trade_pct 동적 튜닝 + 심볼별 마진 모니터링
  Phase C: UI/Insight 고도화 + 프로파일 마진 튜닝

Signal Tick (at SIGNAL_TF boundary):
  1. Universe refresh
  2. Datafeed collection
  3. TrendScore computation
  4. Sizing → target_qty (risk-based + leverage computation)
  5. Risk guard check (drawdown + available balance + concurrent symbols)

Exec Tick (every execution_tick_sec):
  0. Pre-flight: margin mode + leverage per symbol
  1. Current positions fetch
  2. Delta = target − current
  3. ExecutionEngine.tick()
  4. Bracket management (actual position qty)
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
        
        # Fetch available balance for insight
        _available_bal = 0.0
        try:
            _acct = binance_client.account()
            _available_bal = float(_acct.get("availableBalance", 0) or 0)
        except Exception:
            pass

        risk_status = {
            "equity": round(self._equity, 2),
            "peak_equity": round(self._peak_equity, 2),
            "drawdown_pct": round(drawdown_pct, 4),
            "drawdown_threshold": self._config.drawdown_kill_pct,
            "gross_leverage": round(gross_leverage, 2),
            "max_leverage": self._config.effective_leverage_target,
            "warnings": self._risk_warnings.copy(),
            "kill_active": not self._running and "kill" in self._status_reason.lower(),
            "margin_mode": self._config.margin_mode,
            "available_balance": round(_available_bal, 2),
            "reserve_buffer_pct": self._config.reserve_margin_buffer_pct,
            "max_symbol_leverage": self._config.max_symbol_leverage,
            "risk_per_trade_pct": self._config.risk_per_trade_pct,
            "max_concurrent_symbols": self._config.max_concurrent_symbols,
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

            # ATR map for risk-based sizing
            _atr_map = fetch_atr_map(symbols, cfg.signal_tf, cfg.stop_atr_window)

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
                risk_per_trade_pct=cfg.risk_per_trade_pct,
                max_symbol_leverage=cfg.max_symbol_leverage,
                min_symbol_leverage=cfg.min_symbol_leverage,
                stop_k=cfg.stop_k,
                atr_map=_atr_map,
            )

            # 5. Risk guard
            positions = []
            try:
                raw_positions = binance_client.position_information()
                positions = [p for p in raw_positions if float(p.get("positionAmt", 0)) != 0]
            except Exception:
                pass

            # Fetch available balance for reserve-buffer check
            _available_balance = 0.0
            try:
                acct_info = binance_client.account()
                _available_balance = float(acct_info.get("availableBalance", 0) or 0)
            except Exception:
                pass

            risk = run_all_checks(
                current_equity=self._equity,
                peak_equity=self._peak_equity,
                positions=positions,
                kill_pct=cfg.drawdown_kill_pct,
                max_gross_leverage=cfg.effective_leverage_target * 2,
                available_balance=_available_balance,
                reserve_buffer_pct=cfg.reserve_margin_buffer_pct,
                max_concurrent_symbols=cfg.max_concurrent_symbols,
            )
            if risk.kill:
                self._running = False
                self._stop_event.set()
                self._status_reason = f"Kill switch: {risk.reason}"
                ledger.record("risk_kill", {"reason": risk.reason})
                logger.error("RISK KILL: %s", risk.reason)
                return
            if not risk.ok and not risk.kill:
                # Risk check failed (non-fatal): block new entries
                if self._sizing_result:
                    self._sizing_result.targets.clear()
                    self._sizing_result.drop_reason = "entry_skipped_risk_budget"
                ledger.record("risk_block_entry", {"reason": risk.reason})
                logger.warning("Risk block entry: %s", risk.reason)

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
            # Get detailed reason from sizing if available
            drop_reason = getattr(self._sizing_result, 'drop_reason', '') if self._sizing_result else 'no_sizing_result'
            
            reason_messages = {
                'zero_equity': 'No equity available',
                'all_deadzone_or_no_vol': 'All signals in deadzone or missing volatility data',
                'zero_abs_sum': 'Zero weight sum after normalization',
                'all_below_min_weight': 'All candidates below minimum weight threshold',
                'fallback_below_min_notional': 'Even fallback position below minimum order size',
                'fallback_no_price': 'Fallback failed: price unavailable',
                'no_signals_for_fallback': 'No viable signals for fallback',
                'fallback_all_untradable': 'All fallback candidates untradable',
                'exceeds_leverage_cap': 'Minimum order exceeds leverage budget',
                'all_filtered_unknown': 'All targets filtered for unknown reason',
                # Isolated margin / risk guard reasons
                'margin_mode_switch_failed': 'Failed to switch margin mode (check Binance position)',
                'isolated_margin_insufficient': 'Insufficient isolated margin for entry',
                'symbol_leverage_clamped': 'Symbol leverage clamped to min/max bounds',
                'entry_skipped_risk_budget': 'Entry skipped: risk budget exceeded',
                'available_balance_low': 'Available balance below reserve buffer',
                'max_concurrent_reached': 'Max concurrent symbols limit reached',
            }
            
            detailed_message = reason_messages.get(drop_reason, f'Sizing produced no targets ({drop_reason or "deadzone/top-k/min_weight"})')

            # Append drop_meta summary when available (multi-candidate fallback)
            drop_meta = getattr(self._sizing_result, 'drop_meta', {}) if self._sizing_result else {}
            if drop_meta:
                tried = drop_meta.get('tried', 0)
                summary = drop_meta.get('summary', '')
                if tried and summary:
                    detailed_message += f' (tried {tried} symbols: {summary})'
            
            ledger.record(
                "exec_tick_skip",
                {
                    "reason": drop_reason or "no_targets",
                    "message": detailed_message,
                    "meta": drop_meta,
                },
            )
            logger.info("exec_tick: no targets, reason=%s, meta=%s", drop_reason or "unknown", drop_meta or "")
            return

        try:
            # Margin mode & leverage are now handled by ExecutionEngine._preflight()
            # per symbol before each order placement (ISOLATED mode support).
            cfg = self._config

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
