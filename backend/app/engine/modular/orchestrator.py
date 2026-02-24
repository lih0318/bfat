"""
Orchestrator: signal -> risk -> execution -> position management -> performance logging -> optimizer.
No global shared state. Reloads config each tick for hot-update from optimizer.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.engine.modular.config_model import ModularConfig, load_modular_config
from app.engine.accounting import ledger
from app.engine.modular.execution_engine import (
    execute_risk_plan,
    fetch_market_snapshot,
    fetch_trade_history,
    risk_to_plan,
)
from app.engine.modular.optimizer_engine import OptimizerState, run as run_optimizer
from app.engine.modular.risk_engine import RiskContext, run as run_risk
from app.engine.modular.signal_engine import run as run_signal
from app.engine.modular.types import PerformanceLog, RiskResult, SignalResult

logger = logging.getLogger(__name__)


def tick(
    config: ModularConfig,
    optimizer_state: Optional[OptimizerState] = None,
    peak_equity: float = 0.0,
) -> tuple[dict[str, Any], Optional[OptimizerState], float]:
    """
    Single pipeline tick. Returns (status_dict, new_optimizer_state, new_peak_equity).
    Reloads config each tick so optimizer updates apply without restart.
    """
    config = load_modular_config()
    status: dict[str, Any] = {"reason": "ok", "step": "fetch"}
    new_opt_state = optimizer_state
    new_peak = peak_equity

    try:
        snapshot = fetch_market_snapshot(config)
        status["symbols_count"] = len(snapshot.symbols)
        status["equity"] = snapshot.equity
        if not snapshot.symbols:
            status["reason"] = "no_symbols"
            status["universe_size"] = 0
            status["active_symbols"] = []
            status["gross_exposure"] = 0.0
            new_peak = max(new_peak, snapshot.equity)
            trade_history = fetch_trade_history()
            perf = PerformanceLog(equity=snapshot.equity, ts=time.time())
            upd, new_opt_state = run_optimizer(trade_history, new_opt_state, perf, 0.0)
            return status, new_opt_state, new_peak

        signals = run_signal(snapshot, config)
        status["step"] = "signal"
        status["universe_size"] = len(snapshot.symbols)
        if not signals.outputs and not signals.snapshots:
            status["reason"] = "no_signals"
            status["active_symbols"] = []
            status["gross_exposure"] = 0.0
            return status, new_opt_state, new_peak

        drawdown_pct = 100.0 * (1.0 - snapshot.equity / new_peak) if new_peak > 0 else 0.0
        btc_out = (signals.outputs or {}).get("BTCUSDT")
        btc_regime = getattr(btc_out, "market_regime", "neutral") if btc_out else "neutral"
        recent_winrate = 0.5
        if new_opt_state and new_opt_state.logs:
            recent = new_opt_state.logs[-10:]
            recent_winrate = sum(p.win_rate for p in recent) / len(recent) if recent else 0.5
        risk_ctx = RiskContext(
            current_drawdown_pct=drawdown_pct,
            btc_regime=btc_regime,
            recent_winrate=recent_winrate,
        )
        risk = run_risk(signals, snapshot, config, context=risk_ctx)
        status["step"] = "risk"
        status["decisions_allowed"] = sum(1 for d in risk.decisions.values() if d.allowed)
        status["active_symbols"] = [s for s, d in risk.decisions.items() if d.allowed]
        plan = risk_to_plan(risk, snapshot)
        status["gross_exposure"] = plan.gross_notional
        if not any(d.allowed for d in risk.decisions.values()):
            status["reason"] = "no_trades_allowed"
            status["equity"] = snapshot.equity
            status["gross_exposure"] = 0.0
            new_peak = max(new_peak, snapshot.equity)
            trade_history = fetch_trade_history()
            drawdown = 100.0 * (1.0 - snapshot.equity / new_peak) if new_peak > 0 else 0.0
            perf = PerformanceLog(equity=snapshot.equity, drawdown_pct=drawdown, ts=time.time())
            upd, new_opt_state = run_optimizer(trade_history, new_opt_state, perf, drawdown)
            if upd.overrides:
                status["optimizer"] = upd.overrides
            return status, new_opt_state, new_peak

        result = execute_risk_plan(risk, snapshot, config)
        status["step"] = "execution"
        status["success"] = result.success
        if result.errors:
            status["reason"] = "; ".join(result.errors[:2])

        # Record modular execution activity to ledger for UI
        for act in result.activity:
            et = act.get("type", "activity")
            sym = act.get("symbol", "")
            if et == "order":
                ledger.record("order", {
                    "symbol": sym,
                    "side": act.get("side", ""),
                    "qty": act.get("qty", 0),
                    "order_type": "MARKET",
                })
            elif et == "error":
                ledger.record("exec_tick_skip", {"symbol": sym, "message": act.get("message", str(act))})
            else:
                msg = act.get("message") or f"{et}: {sym}"
                ledger.record(et, {"symbol": sym, "message": msg, **act})

        equity = snapshot.equity
        new_peak = max(new_peak, equity)
        drawdown = 100.0 * (1.0 - equity / new_peak) if new_peak > 0 else 0.0

        trade_history = fetch_trade_history()
        perf = PerformanceLog(
            equity=equity,
            drawdown_pct=drawdown,
            trade_count=len(risk.decisions),
            ts=time.time(),
        )
        upd, new_opt_state = run_optimizer(trade_history, new_opt_state, perf, drawdown)
        if upd.overrides:
            status["optimizer"] = upd.overrides

        status["equity"] = equity
        status["peak_equity"] = new_peak
        return status, new_opt_state, new_peak

    except Exception as exc:
        logger.exception("orchestrator tick failed: %s", exc)
        status["reason"] = str(exc)
        return status, new_opt_state, new_peak
