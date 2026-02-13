"""
Position sizing: volatility targeting, Top-K concentration, weight normalisation.

Pipeline:
  1. w_raw = TrendScore / vol × liquidity_penalty × rsi_scale × funding_scale
  2. Top-K selection (with turnover guard)
  3. min_weight_floor / max_weight_cap
  4. Normalise → gross_notional → target_qty
  5. exchangeInfo stepSize / minNotional validation
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.engine.signals import SignalSnapshot
from app.services.exchange_info import ExchangeInfoCache

logger = logging.getLogger(__name__)


@dataclass
class TargetPosition:
    symbol: str
    side: str  # "LONG" or "SHORT"
    weight: float = 0.0  # normalised portfolio weight (signed)
    target_qty: float = 0.0  # absolute quantity (always positive)
    target_notional: float = 0.0
    trend_score: float = 0.0


@dataclass
class SizingResult:
    targets: dict[str, TargetPosition] = field(default_factory=dict)
    equity: float = 0.0
    gross_notional: float = 0.0


def compute_target_positions(
    snapshots: dict[str, SignalSnapshot],
    vol_map: dict[str, float],
    penalty_map: dict[str, float],
    equity: float,
    target_vol: float = 0.10,
    leverage_target: float = 1.0,
    top_k_enabled: bool = True,
    top_k: int = 5,
    replace_threshold: float = 0.20,
    min_weight_floor: float = 0.02,
    max_weight_cap: float = 0.40,
    current_symbols: set[str] | None = None,
) -> SizingResult:
    """
    Compute target positions for all symbols in the signal universe.

    Parameters
    ----------
    snapshots : dict of SignalSnapshot (output of signals module)
    vol_map : {symbol: annualized vol}
    penalty_map : {symbol: liquidity_penalty}  (0..1)
    equity : total wallet balance in USDT
    current_symbols : symbols already in portfolio (for turnover guard)

    Returns
    -------
    SizingResult with target positions
    """
    result = SizingResult(equity=equity)
    if equity <= 0:
        return result

    # Step 1: raw weights
    raw_weights: dict[str, float] = {}
    for sym, snap in snapshots.items():
        if snap.final_score == 0:
            continue
        vol = vol_map.get(sym, 0.0)
        if vol <= 0:
            continue
        penalty = penalty_map.get(sym, 1.0)
        w = snap.final_score / vol * penalty
        raw_weights[sym] = w

    if not raw_weights:
        return result

    # Step 2: Top-K selection
    if top_k_enabled and len(raw_weights) > top_k:
        sorted_by_abs = sorted(raw_weights.items(), key=lambda x: abs(x[1]), reverse=True)
        current_symbols = current_symbols or set()
        selected: dict[str, float] = {}
        for sym, w in sorted_by_abs:
            if len(selected) >= top_k:
                # Turnover guard: keep incumbents if new entrant doesn't beat them by threshold
                if sym in current_symbols:
                    # incumbent wants to stay — check if it would be replaced
                    weakest_abs = min(abs(v) for v in selected.values())
                    if abs(w) > weakest_abs:
                        # Replace the weakest
                        weakest_sym = min(selected, key=lambda s: abs(selected[s]))
                        del selected[weakest_sym]
                        selected[sym] = w
                break
            selected[sym] = w
        raw_weights = selected

    # Step 3: min_weight_floor & max_weight_cap
    abs_sum = sum(abs(w) for w in raw_weights.values())
    if abs_sum <= 0:
        return result

    normalised: dict[str, float] = {}
    for sym, w in raw_weights.items():
        nw = w / abs_sum  # normalise to sum |w| = 1
        # Apply floor
        if abs(nw) < min_weight_floor:
            continue  # drop below floor
        # Apply cap
        if abs(nw) > max_weight_cap:
            nw = math.copysign(max_weight_cap, nw)
        normalised[sym] = nw

    if not normalised:
        return result

    # Re-normalise after floor/cap
    abs_sum2 = sum(abs(w) for w in normalised.values())
    if abs_sum2 > 0:
        scale = 1.0 / abs_sum2
        normalised = {s: w * scale for s, w in normalised.items()}

    # Step 4: gross_notional from vol targeting
    # gross_notional = equity × target_vol / avg_weighted_vol
    avg_vol = 0.0
    for sym, w in normalised.items():
        sv = vol_map.get(sym, target_vol)
        avg_vol += abs(w) * sv
    if avg_vol <= 0:
        avg_vol = target_vol

    gross_notional = equity * target_vol / avg_vol
    # Cap by leverage target
    max_notional = equity * leverage_target
    if gross_notional > max_notional:
        gross_notional = max_notional

    result.gross_notional = gross_notional

    # Step 5: target_qty per symbol with exchangeInfo validation
    for sym, w in normalised.items():
        notional = gross_notional * abs(w)
        side = "LONG" if w > 0 else "SHORT"

        # Get current price for qty calculation
        snap = snapshots.get(sym)
        if not snap:
            continue

        # Use last close from signal (approximate)
        try:
            bt = ExchangeInfoCache.get_symbol_filters(sym)
            # We need current price — use klines endpoint briefly
            from app.services.binance_client import binance_client
            klines = binance_client.klines(symbol=sym, interval="1m", limit=1)
            price = float(klines[-1][4]) if klines else 0.0
        except Exception:
            price = 0.0

        if price <= 0:
            continue

        qty = notional / price
        qty = ExchangeInfoCache.round_quantity(sym, qty)

        # Min notional check
        filters = ExchangeInfoCache.get_symbol_filters(sym)
        min_notional = filters.get("min_notional", 5.0)
        if qty * price < min_notional:
            logger.debug("sizing: %s below min notional (%.2f < %.2f)", sym, qty * price, min_notional)
            continue

        tp = TargetPosition(
            symbol=sym,
            side=side,
            weight=w,
            target_qty=qty,
            target_notional=notional,
            trend_score=snap.final_score,
        )
        result.targets[sym] = tp

    return result
