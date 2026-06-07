"""
Optimizer engine: adjusts parameters from trade history.
Run every 50 trades. Objectives: maximize Sharpe, minimize drawdown.
Adjustable: risk_per_trade (0.3%~0.8%), ATR stop mult (1.2~2.2),
            BTC pullback tolerance, ALT RSI bounds.
Updates modular_engine.json. Other engines read via load_modular_config each tick.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.engine.modular.config_model import (
    ModularConfig,
    load_modular_config,
    save_modular_config,
)
from app.engine.modular.types import ConfigUpdate, PerformanceLog, TradeRecord

logger = logging.getLogger(__name__)

RUN_EVERY_N_TRADES = 50
LOOKBACK_TRADES = 100

# Bounds for adjustable params
RISK_MIN = 0.003
RISK_MAX = 0.008
ATR_STOP_MIN = 1.2
ATR_STOP_MAX = 2.2
PULLBACK_MIN = 0.002
PULLBACK_MAX = 0.015
ALT_RSI_LONG_MIN = 20
ALT_RSI_LONG_MAX = 40
ALT_RSI_SHORT_MIN = 60
ALT_RSI_SHORT_MAX = 80

WINRATE_TIGHTEN_THRESHOLD = 0.40
DRAWDOWN_REDUCE_THRESHOLD = 10.0
TP_INCREASE_THRESHOLD = 1.0  # avg_loss/avg_win > 1


@dataclass
class OptimizerState:
    """Tracks trade count, last run, performance history."""
    logs: list[PerformanceLog] = field(default_factory=list)
    trade_history: list[TradeRecord] = field(default_factory=list)
    last_run_trade_count: int = 0
    max_logs: int = 500
    prev_drawdown_pct: float = 0.0


def _sharpe_from_trades(trades: list[TradeRecord], annualize_factor: float = 252 * 24) -> float:
    """Sharpe = mean(pnl) / std(pnl) * sqrt(annualize), using returns as proxy."""
    if len(trades) < 2:
        return 0.0
    pnls = [t.pnl for t in trades]
    mean_pnl = sum(pnls) / len(pnls)
    var = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
    std = math.sqrt(var)
    if std <= 0:
        return 0.0
    return (mean_pnl / std) * math.sqrt(annualize_factor)


def _winrate(trades: list[TradeRecord]) -> float:
    if not trades:
        return 0.5
    wins = sum(1 for t in trades if t.win)
    return wins / len(trades)


def _avg_win_loss(trades: list[TradeRecord]) -> tuple[float, float]:
    wins = [t.pnl for t in trades if t.win]
    losses = [t.pnl for t in trades if not t.win]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    return avg_win, avg_loss


def run(
    trade_history: list[TradeRecord],
    state: OptimizerState | None = None,
    perf_log: PerformanceLog | None = None,
    current_drawdown_pct: float = 0.0,
) -> tuple[ConfigUpdate, OptimizerState]:
    """
    Run optimizer every 50 trades. Use trade_history for analysis.
    Updates config.json. Other engines reload via load_modular_config each tick.
    """
    state = state or OptimizerState()
    update = ConfigUpdate()

    if perf_log:
        state.logs.append(perf_log)
        if len(state.logs) > state.max_logs:
            state.logs = state.logs[-state.max_logs:]

    trade_count = len(trade_history)
    new_trades_since_run = trade_count - state.last_run_trade_count

    if new_trades_since_run < RUN_EVERY_N_TRADES:
        return update, state

    state.last_run_trade_count = trade_count
    recent = trade_history[-LOOKBACK_TRADES:]
    if len(recent) < 20:
        return update, state

    cfg = load_modular_config()
    overrides: dict[str, Any] = {}

    winrate = _winrate(recent)
    avg_win, avg_loss = _avg_win_loss(recent)
    loss_win_ratio = avg_loss / avg_win if avg_win > 0 else 0.0
    drawdown_increasing = current_drawdown_pct > state.prev_drawdown_pct
    state.prev_drawdown_pct = current_drawdown_pct

    sharpe = _sharpe_from_trades(recent)

    # If winrate < 40%: tighten entry conditions
    if winrate < WINRATE_TIGHTEN_THRESHOLD:
        overrides["deadzone_threshold"] = round(
            min(0.25, cfg.deadzone_threshold + 0.02), 2
        )
        overrides["btc_pullback_tolerance_pct"] = round(
            max(PULLBACK_MIN, cfg.btc_pullback_tolerance_pct - 0.001), 4
        )
        overrides["alt_rsi_long_max"] = round(
            max(ALT_RSI_LONG_MIN, cfg.alt_rsi_long_max - 2), 1
        )
        overrides["alt_rsi_short_min"] = round(
            min(ALT_RSI_SHORT_MAX, cfg.alt_rsi_short_min + 2), 1
        )
        logger.info(
            "optimizer: tightening entry (winrate=%.1f%%)",
            winrate * 100,
        )

    # If avg loss > avg win: increase take-profit distance
    if loss_win_ratio > TP_INCREASE_THRESHOLD and avg_win > 0:
        new_tp1 = min(3.0, cfg.tp1_r_multiple * 1.15)
        new_tp2 = min(5.0, cfg.tp2_r_multiple * 1.15)
        overrides["tp1_r_multiple"] = round(new_tp1, 2)
        overrides["tp2_r_multiple"] = round(new_tp2, 2)
        logger.info(
            "optimizer: increasing TP distance (avg_loss/avg_win=%.2f)",
            loss_win_ratio,
        )

    # If drawdown increasing: reduce risk
    if drawdown_increasing and current_drawdown_pct > DRAWDOWN_REDUCE_THRESHOLD:
        new_risk = max(RISK_MIN, cfg.risk_per_trade_pct * 0.85)
        overrides["risk_per_trade_pct"] = round(new_risk, 4)
        logger.info(
            "optimizer: reducing risk (drawdown %.1f%% increasing)",
            current_drawdown_pct,
        )

    # Sharpe / drawdown objectives: gentle tuning
    if sharpe > 1.0 and current_drawdown_pct < 5.0:
        if cfg.risk_per_trade_pct < RISK_MAX:
            new_risk = min(RISK_MAX, cfg.risk_per_trade_pct * 1.05)
            overrides["risk_per_trade_pct"] = round(new_risk, 4)
    elif sharpe < 0 and current_drawdown_pct > 15.0:
        new_risk = max(RISK_MIN, cfg.risk_per_trade_pct * 0.9)
        new_atr = max(ATR_STOP_MIN, cfg.atr_stop_mult - 0.1)
        overrides["risk_per_trade_pct"] = round(new_risk, 4)
        overrides["atr_stop_mult"] = round(new_atr, 2)

    to_remove = [
        k for k, v in overrides.items()
        if k not in cfg.model_dump() or getattr(cfg, k, None) == v
    ]
    for k in to_remove:
        overrides.pop(k, None)

    if overrides:
        d = cfg.model_dump()
        d.update(overrides)
        try:
            new_cfg = ModularConfig.model_validate(d)
            save_modular_config(new_cfg)
            update.overrides = overrides
            logger.info("optimizer: updated config %s", list(overrides.keys()))
        except Exception as exc:
            logger.warning("optimizer: config update failed: %s", exc)

    return update, state
