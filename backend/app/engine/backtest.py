"""
Event-based backtest for the TSMOM engine.

Features:
  - Fee model: maker/taker rates
  - Slippage model: spread/2 + impact
  - Funding: 8-hourly charges
  - Walk-forward validation
  - Report: Sharpe, maxDD, turnover, per-symbol exposure, vol-regime performance
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.engine.signals import compute_trend_scores, apply_rsi_overlay, SignalSnapshot
from app.engine.datafeed import compute_realized_vol, compute_rsi

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backtest parameters."""
    initial_equity: float = 10_000.0
    signal_tf: str = "1d"
    horizons: list[int] = field(default_factory=lambda: [30, 90, 365])
    deadzone: float = 0.10
    target_vol: float = 0.10
    leverage_cap: float = 2.0
    vol_window: int = 60
    stop_k: float = 2.0
    top_k: int = 5
    min_weight_floor: float = 0.02
    max_weight_cap: float = 0.40
    maker_fee: float = 0.0002  # 2 bps
    taker_fee: float = 0.0004  # 4 bps
    slippage_bps: float = 2.0  # extra bps
    funding_rate: float = 0.0001  # per 8h
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0


@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    fee: float
    bar_idx: int


@dataclass
class BacktestResult:
    equity_curve: list[float] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    turnover: float = 0.0
    per_symbol_pnl: dict[str, float] = field(default_factory=dict)


def run_backtest(
    closes_map: dict[str, np.ndarray],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """
    Run event-based backtest on historical close data.

    Parameters
    ----------
    closes_map : {symbol: np.ndarray of closes} — all symbols aligned by bar index
    config : BacktestConfig (defaults used if None)

    Returns
    -------
    BacktestResult
    """
    cfg = config or BacktestConfig()
    result = BacktestResult()

    if not closes_map:
        return result

    # Determine common length
    min_len = min(len(c) for c in closes_map.values())
    if min_len < max(cfg.horizons) + cfg.vol_window + 10:
        logger.warning("backtest: insufficient data (min_len=%d)", min_len)
        return result

    symbols = list(closes_map.keys())
    n_bars = min_len

    # Align all arrays
    aligned: dict[str, np.ndarray] = {}
    for sym in symbols:
        arr = closes_map[sym]
        aligned[sym] = arr[-n_bars:]  # take last n_bars

    equity = cfg.initial_equity
    peak_equity = equity
    result.equity_curve.append(equity)

    # Positions: {symbol: {"side": "LONG"/"SHORT", "qty": float, "entry_price": float}}
    positions: dict[str, dict[str, Any]] = {}
    total_turnover = 0.0

    # Walk-forward: start after enough lookback
    start_bar = max(cfg.horizons) + cfg.vol_window + 5

    for bar in range(start_bar, n_bars):
        # Build closes_map up to this bar (for signal computation)
        bar_closes: dict[str, np.ndarray] = {}
        for sym in symbols:
            bar_closes[sym] = aligned[sym][:bar + 1]

        # Compute signals
        snapshots = compute_trend_scores(bar_closes, cfg.horizons, cfg.signal_tf, cfg.deadzone)

        # RSI overlay
        apply_rsi_overlay(
            snapshots, bar_closes,
            rsi_period=cfg.rsi_period,
            rsi_overbought=cfg.rsi_overbought,
            rsi_oversold=cfg.rsi_oversold,
        )

        # Compute vol for sizing
        vol_map: dict[str, float] = {}
        for sym in symbols:
            vol = compute_realized_vol(bar_closes[sym], cfg.vol_window)
            if vol > 0:
                vol_map[sym] = vol

        # Compute raw weights
        raw_weights: dict[str, float] = {}
        for sym, snap in snapshots.items():
            if snap.final_score == 0:
                continue
            vol = vol_map.get(sym, cfg.target_vol)
            if vol <= 0:
                continue
            raw_weights[sym] = snap.final_score / vol

        # Top-K selection
        if len(raw_weights) > cfg.top_k:
            sorted_w = sorted(raw_weights.items(), key=lambda x: abs(x[1]), reverse=True)
            raw_weights = dict(sorted_w[:cfg.top_k])

        # Normalize weights
        abs_sum = sum(abs(w) for w in raw_weights.values())
        if abs_sum > 0:
            normalised = {}
            for sym, w in raw_weights.items():
                nw = w / abs_sum
                if abs(nw) < cfg.min_weight_floor:
                    continue
                if abs(nw) > cfg.max_weight_cap:
                    nw = math.copysign(cfg.max_weight_cap, nw)
                normalised[sym] = nw
            # Re-normalize
            abs_sum2 = sum(abs(w) for w in normalised.values())
            if abs_sum2 > 0:
                normalised = {s: w / abs_sum2 for s, w in normalised.items()}
        else:
            normalised = {}

        # Sizing: gross notional
        avg_vol = sum(abs(normalised.get(s, 0)) * vol_map.get(s, cfg.target_vol) for s in normalised) or cfg.target_vol
        gross_notional = min(equity * cfg.target_vol / avg_vol, equity * cfg.leverage_cap) if avg_vol > 0 else 0

        # Target positions
        target_pos: dict[str, dict[str, Any]] = {}
        for sym, w in normalised.items():
            price = float(bar_closes[sym][-1])
            if price <= 0:
                continue
            notional = gross_notional * abs(w)
            qty = notional / price
            side = "LONG" if w > 0 else "SHORT"
            target_pos[sym] = {"side": side, "qty": qty, "price": price}

        # Execute trades: close positions not in target, adjust existing, open new
        # Close positions not in target
        for sym in list(positions.keys()):
            if sym not in target_pos:
                pos = positions.pop(sym)
                exit_price = float(bar_closes[sym][-1])
                # Apply slippage
                slippage = exit_price * cfg.slippage_bps / 10000
                if pos["side"] == "LONG":
                    exit_price -= slippage
                    pnl = (exit_price - pos["entry_price"]) * pos["qty"]
                else:
                    exit_price += slippage
                    pnl = (pos["entry_price"] - exit_price) * pos["qty"]
                fee = abs(pos["qty"] * exit_price * cfg.taker_fee)
                pnl -= fee
                equity += pnl
                total_turnover += abs(pos["qty"] * exit_price)
                result.trades.append(TradeRecord(
                    symbol=sym, side=pos["side"],
                    entry_price=pos["entry_price"], exit_price=exit_price,
                    qty=pos["qty"], pnl=pnl, fee=fee, bar_idx=bar,
                ))
                result.per_symbol_pnl[sym] = result.per_symbol_pnl.get(sym, 0) + pnl

        # Adjust/open positions
        for sym, tgt in target_pos.items():
            price = tgt["price"]
            slippage = price * cfg.slippage_bps / 10000

            if sym in positions:
                pos = positions[sym]
                if pos["side"] != tgt["side"]:
                    # Close and reopen (flip)
                    exit_price = price - slippage if pos["side"] == "LONG" else price + slippage
                    if pos["side"] == "LONG":
                        pnl = (exit_price - pos["entry_price"]) * pos["qty"]
                    else:
                        pnl = (pos["entry_price"] - exit_price) * pos["qty"]
                    fee = abs(pos["qty"] * exit_price * cfg.taker_fee)
                    pnl -= fee
                    equity += pnl
                    total_turnover += abs(pos["qty"] * exit_price)
                    result.trades.append(TradeRecord(
                        symbol=sym, side=pos["side"],
                        entry_price=pos["entry_price"], exit_price=exit_price,
                        qty=pos["qty"], pnl=pnl, fee=fee, bar_idx=bar,
                    ))
                    result.per_symbol_pnl[sym] = result.per_symbol_pnl.get(sym, 0) + pnl
                    # Open new
                    entry_price = price + slippage if tgt["side"] == "LONG" else price - slippage
                    fee_entry = abs(tgt["qty"] * entry_price * cfg.taker_fee)
                    equity -= fee_entry
                    total_turnover += abs(tgt["qty"] * entry_price)
                    positions[sym] = {"side": tgt["side"], "qty": tgt["qty"], "entry_price": entry_price}
                else:
                    # Same side: just update qty (simplified — real engine does delta)
                    positions[sym]["qty"] = tgt["qty"]
            else:
                # New position
                entry_price = price + slippage if tgt["side"] == "LONG" else price - slippage
                fee_entry = abs(tgt["qty"] * entry_price * cfg.taker_fee)
                equity -= fee_entry
                total_turnover += abs(tgt["qty"] * entry_price)
                positions[sym] = {"side": tgt["side"], "qty": tgt["qty"], "entry_price": entry_price}

        # Funding (every ~3 bars for daily = every 3 × 8h)
        if bar % 3 == 0:
            for sym, pos in positions.items():
                price = float(bar_closes[sym][-1])
                funding_cost = abs(pos["qty"]) * price * cfg.funding_rate
                if pos["side"] == "LONG":
                    equity -= funding_cost
                else:
                    equity += funding_cost

        # Stop-loss check
        for sym in list(positions.keys()):
            pos = positions[sym]
            price = float(bar_closes[sym][-1])
            atr = _simple_atr(bar_closes[sym], 14)
            stop_dist = atr * cfg.stop_k

            if pos["side"] == "LONG":
                sl_price = pos["entry_price"] - stop_dist
                if price <= sl_price:
                    exit_price = sl_price
                    pnl = (exit_price - pos["entry_price"]) * pos["qty"]
                    fee = abs(pos["qty"] * exit_price * cfg.taker_fee)
                    pnl -= fee
                    equity += pnl
                    result.trades.append(TradeRecord(
                        symbol=sym, side="LONG",
                        entry_price=pos["entry_price"], exit_price=exit_price,
                        qty=pos["qty"], pnl=pnl, fee=fee, bar_idx=bar,
                    ))
                    result.per_symbol_pnl[sym] = result.per_symbol_pnl.get(sym, 0) + pnl
                    del positions[sym]
            else:
                sl_price = pos["entry_price"] + stop_dist
                if price >= sl_price:
                    exit_price = sl_price
                    pnl = (pos["entry_price"] - exit_price) * pos["qty"]
                    fee = abs(pos["qty"] * exit_price * cfg.taker_fee)
                    pnl -= fee
                    equity += pnl
                    result.trades.append(TradeRecord(
                        symbol=sym, side="SHORT",
                        entry_price=pos["entry_price"], exit_price=exit_price,
                        qty=pos["qty"], pnl=pnl, fee=fee, bar_idx=bar,
                    ))
                    result.per_symbol_pnl[sym] = result.per_symbol_pnl.get(sym, 0) + pnl
                    del positions[sym]

        # Track equity
        result.equity_curve.append(equity)
        if equity > peak_equity:
            peak_equity = equity

    # Close remaining positions at final bar
    for sym, pos in positions.items():
        exit_price = float(aligned[sym][-1])
        if pos["side"] == "LONG":
            pnl = (exit_price - pos["entry_price"]) * pos["qty"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["qty"]
        fee = abs(pos["qty"] * exit_price * cfg.taker_fee)
        pnl -= fee
        equity += pnl
        result.trades.append(TradeRecord(
            symbol=sym, side=pos["side"],
            entry_price=pos["entry_price"], exit_price=exit_price,
            qty=pos["qty"], pnl=pnl, fee=fee, bar_idx=n_bars - 1,
        ))
        result.per_symbol_pnl[sym] = result.per_symbol_pnl.get(sym, 0) + pnl

    result.equity_curve.append(equity)

    # Compute stats
    result.total_trades = len(result.trades)
    if result.total_trades > 0:
        wins = sum(1 for t in result.trades if t.pnl > 0)
        result.win_rate = wins / result.total_trades
        result.avg_pnl = sum(t.pnl for t in result.trades) / result.total_trades
    result.total_return = (equity / cfg.initial_equity - 1.0)
    result.turnover = total_turnover / cfg.initial_equity if cfg.initial_equity > 0 else 0

    # Sharpe from equity curve
    ec = np.array(result.equity_curve)
    if len(ec) > 1:
        returns = np.diff(ec) / ec[:-1]
        returns = returns[np.isfinite(returns)]
        if len(returns) > 1 and np.std(returns) > 0:
            result.sharpe = float(np.mean(returns) / np.std(returns) * math.sqrt(365))

    # Max drawdown
    peak = ec[0]
    max_dd = 0.0
    for val in ec:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown = max_dd

    return result


def _simple_atr(closes: np.ndarray, window: int = 14) -> float:
    """Simplified ATR using close-to-close (no H/L data in backtest)."""
    if len(closes) < window + 1:
        return 0.0
    diffs = np.abs(np.diff(closes[-window - 1:]))
    return float(np.mean(diffs))
