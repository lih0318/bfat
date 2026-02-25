"""
Modular engine runner: signal -> risk -> execution -> position management -> performance logging -> optimizer (periodic).
No global shared state. No Binance calls — all via execution_engine.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from app.engine.accounting import ledger
from app.engine.modular.config_model import ModularConfig, load_modular_config
from app.engine.modular.orchestrator import tick
from app.engine.modular.types import OrderPlan, SignalResult

logger = logging.getLogger(__name__)


class ModularRunner:
    """Orchestrates: signal -> risk -> execution -> pos_mgmt -> perf_log -> optimizer (periodic)."""

    def __init__(self) -> None:
        self._config: ModularConfig = load_modular_config()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status_reason: str = "ok"
        self._last_status: dict[str, Any] = {}
        self._optimizer_state = None
        self._peak_equity: float = 0.0
        self._last_tick_ts: float = 0.0
        self._tick_count: int = 0
        self._last_signals: Optional[SignalResult] = None
        self._last_plan: Optional[OrderPlan] = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def config(self) -> ModularConfig:
        return self._config

    @config.setter
    def config(self, cfg: ModularConfig) -> None:
        self._config = cfg

    def get_status(self) -> dict[str, Any]:
        """Return status in format compatible with legacy EngineRunner for frontend."""
        equity = self._last_status.get("equity", 0.0)
        peak = self._last_status.get("peak_equity", 0.0) or self._peak_equity
        profile = "balanced"
        try:
            from app.engine.config_model import load_engine_config
            profile = load_engine_config().profile or "balanced"
        except Exception:
            pass
        return {
            "running": self._running,
            "reason": self._status_reason,
            "profile": profile,
            "symbol": self._config.symbol,
            "active_symbols": self._last_status.get("active_symbols", []),
            "equity": round(equity, 2),
            "peak_equity": round(peak, 2),
            "gross_exposure": round(self._last_status.get("gross_exposure", 0.0), 2),
            "universe_size": self._last_status.get("universe_size", self._last_status.get("symbols_count", 0)),
        }

    def get_insight(self) -> dict[str, Any]:
        """Return insight data for Insight tab. Modular uses unified tick (signal+exec)."""
        now = time.time()
        exec_interval = self._config.execution_tick_sec
        time_since = now - self._last_tick_ts if self._last_tick_ts > 0 else 0
        next_sec = max(0, exec_interval - time_since)

        engine_pulse = {
            "last_signal_tick": self._last_tick_ts,
            "last_exec_tick": self._last_tick_ts,
            "signal_count": self._tick_count,
            "exec_count": self._tick_count,
            "signal_interval_sec": exec_interval,
            "exec_interval_sec": exec_interval,
            "time_since_signal_sec": round(time_since, 1),
            "time_since_exec_sec": round(time_since, 1),
            "next_signal_sec": round(next_sec, 1),
            "next_exec_sec": round(next_sec, 1),
            "signal_tf": self._config.signal_tf,
        }

        equity = self._last_status.get("equity", 0.0)
        peak = self._last_status.get("peak_equity", 0.0) or self._peak_equity
        drawdown_pct = 0.0
        if peak > 0:
            drawdown_pct = (peak - equity) / peak
        gross_exp = self._last_status.get("gross_exposure", 0.0)
        gross_leverage = gross_exp / equity if equity > 0 else 0.0

        universe_size = self._last_status.get("universe_size", self._last_status.get("symbols_count", 0))

        return {
            "engine_pulse": engine_pulse,
            "market_summary": {
                "bullish_count": 0,
                "bearish_count": 0,
                "neutral_count": 0,
                "avg_trend_score": 0.0,
                "temperature": "중립",
            },
            "risk_status": {
                "equity": round(equity, 2),
                "peak_equity": round(peak, 2),
                "drawdown_pct": round(drawdown_pct, 4),
                "drawdown_threshold": self._config.drawdown_kill_pct,
                "gross_leverage": round(gross_leverage, 2),
                "max_leverage": self._config.effective_leverage_target,
                "warnings": [],
                "kill_active": False,
                "margin_mode": self._config.margin_mode,
                "available_balance": 0.0,
                "reserve_buffer_pct": self._config.reserve_margin_buffer_pct,
                "max_symbol_leverage": self._config.max_symbol_leverage,
                "risk_per_trade_pct": self._config.risk_per_trade_pct,
                "max_concurrent_symbols": self._config.max_concurrent_symbols,
            },
            "universe_scan": {
                "selected_count": universe_size,
                "excluded": [],
                "total_scanned": universe_size,
            },
            "signals": self.get_signals(),
            "portfolio": self.get_portfolio(),
        }

    def get_signals(self) -> list[dict[str, Any]]:
        """Return full universe TrendScore snapshot (legacy format)."""
        if not self._last_signals or not self._last_signals.snapshots:
            return []
        result = []
        for sym, snap in self._last_signals.snapshots.items():
            result.append({
                "symbol": sym,
                "trend_score_raw": round(snap.trend_score_raw, 4),
                "trend_score": round(snap.trend_score, 4),
                "final_score": round(snap.final_score, 4),
                "rsi": round(snap.rsi, 1),
                "rsi_scale": round(snap.rsi_scale, 3),
                "funding_rate": round(snap.funding_rate, 6),
                "funding_scale": round(snap.funding_scale, 3),
                "horizons": {},
            })
        return result

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Return per-symbol target/weight/TrendScore (legacy format)."""
        if not self._last_plan or not self._last_plan.targets:
            return []
        result = []
        snapshots = self._last_signals.snapshots if self._last_signals else {}
        for sym, tp in self._last_plan.targets.items():
            snap = snapshots.get(sym) if isinstance(snapshots, dict) else None
            trend = tp.trend_score if tp.trend_score != 0 else (snap.final_score if snap else 0.0)
            result.append({
                "symbol": sym,
                "side": tp.side,
                "target_qty": tp.target_qty,
                "weight": round(tp.weight, 4),
                "trend_score": round(trend, 4),
                "target_notional": round(tp.target_notional, 2),
                "rsi": round(snap.rsi, 1) if snap else None,
                "funding_rate": round(snap.funding_rate, 6) if snap else None,
            })
        return result

    def _loop(self) -> None:
        interval = self._config.execution_tick_sec
        while not self._stop_event.is_set():
            status, self._optimizer_state, self._peak_equity, sigs, plan = tick(
                self._config, self._optimizer_state, self._peak_equity,
            )
            if sigs is not None:
                self._last_signals = sigs
            if plan is not None:
                self._last_plan = plan
            self._last_tick_ts = time.time()
            self._tick_count += 1
            self._status_reason = status.get("reason", "ok")
            self._last_status = {
                "reason": self._status_reason,
                "symbols_count": status.get("symbols_count", 0),
                "decisions_allowed": status.get("decisions_allowed", 0),
                "equity": status.get("equity", 0.0),
                "peak_equity": status.get("peak_equity", self._peak_equity),
                "success": status.get("success"),
                "gross_exposure": status.get("gross_exposure", 0.0),
                "active_symbols": status.get("active_symbols", []),
                "universe_size": status.get("universe_size", 0),
            }
            if self._stop_event.wait(timeout=interval):
                break

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._config = load_modular_config()
        profile = "balanced"
        equity = 0.0
        try:
            from app.engine.config_model import load_engine_config
            profile = load_engine_config().profile or "balanced"
            from app.services.binance_client import binance_client
            if binance_client.is_configured():
                acct = binance_client.account()
                equity = float(acct.get("totalWalletBalance", 0) or 0)
        except Exception:
            pass
        ledger.record("engine_start", {"profile": profile, "equity": equity})
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("ModularRunner started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        ledger.record("engine_stop", {"reason": "user"})
        logger.info("ModularRunner stopped")
