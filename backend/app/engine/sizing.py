"""
Position sizing: volatility targeting, Top-K concentration, weight normalisation.

Pipeline:
  1. w_raw = TrendScore / vol ? liquidity_penalty ? rsi_scale ? funding_scale
  2. Dynamic Top-K selection (equity & tradability-aware)
  3. min_weight_floor / max_weight_cap
  4. Normalise ??gross_notional ??target_qty
  5. exchangeInfo stepSize / minNotional validation
  6. Tradability-aware redistribution loop
  7. Strongest-signal fallback if all targets dropped
  8. Final leverage constraint validation
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.engine.signals import SignalSnapshot
from app.services.exchange_info import ExchangeInfoCache

logger = logging.getLogger(__name__)

def _compute_dynamic_top_k(
    raw_weights: dict[str, float],
    equity: float,
    top_k: int,
) -> int:
    """Compute dynamic top-k based on equity and symbol tradability."""
    equity_based_k = top_k
    if equity < 100:
        equity_based_k = 1
    elif equity < 300:
        equity_based_k = 2
    elif equity < 600:
        equity_based_k = 3
    elif equity < 1000:
        equity_based_k = min(top_k, 4)
    
    sorted_by_abs = sorted(raw_weights.items(), key=lambda x: abs(x[1]), reverse=True)
    tradable_count = 0
    estimated_min_notional = 10.0
    
    for sym, w in sorted_by_abs[:top_k]:
        estimated_notional_per_sym = equity / max(top_k, 1)
        if estimated_notional_per_sym >= estimated_min_notional * 1.5:
            tradable_count += 1
    
    tradability_based_k = max(1, tradable_count)
    result_k = min(equity_based_k, tradability_based_k, top_k)
    
    if result_k < top_k:
        logger.info(
            "dynamic_top_k: shrunk from %d to %d (equity=%.2f, equity_k=%d, tradable_k=%d)",
            top_k, result_k, equity, equity_based_k, tradability_based_k
        )
    return result_k


def _get_price(symbol: str) -> float:
    """Fetch current price for a symbol. Returns 0.0 on error."""
    try:
        from app.services.binance_client import binance_client
        klines = binance_client.klines(symbol=symbol, interval="1m", limit=1)
        return float(klines[-1][4]) if klines else 0.0
    except Exception:
        return 0.0



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
    drop_reason: str = ""  # reason if targets is empty


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
        result.drop_reason = "zero_equity"
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
        result.drop_reason = "all_deadzone_or_no_vol"
        return result

    # Step 2: Dynamic Top-K selection
    effective_top_k = top_k
    if top_k_enabled:
        effective_top_k = _compute_dynamic_top_k(raw_weights, equity, top_k)
        
    if top_k_enabled and len(raw_weights) > effective_top_k:
        sorted_by_abs = sorted(raw_weights.items(), key=lambda x: abs(x[1]), reverse=True)
        current_symbols = current_symbols or set()
        selected: dict[str, float] = {}
        for sym, w in sorted_by_abs:
            if len(selected) >= effective_top_k:
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
        result.drop_reason = "zero_abs_sum"
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
        result.drop_reason = "all_below_min_weight"
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

    # Step 6: Tradability-aware redistribution loop
    MAX_REDIST_ITERATIONS = 5
    CONVERGENCE_THRESHOLD = 0.01  # USDT
    
    for iteration in range(MAX_REDIST_ITERATIONS):
        dropped_notional = 0.0
        surviving_targets: dict[str, TargetPosition] = {}
        
        for sym, tp in list(result.targets.items()):
            price = _get_price(sym)
            if price <= 0:
                dropped_notional += tp.target_notional
                continue
            
            filters = ExchangeInfoCache.get_symbol_filters(sym)
            min_notional = filters.get("min_notional", 5.0)
            
            if tp.target_qty * price < min_notional:
                dropped_notional += tp.target_notional
                logger.debug("redist iter %d: dropping %s (%.2f < %.2f)", 
                           iteration, sym, tp.target_qty * price, min_notional)
                continue
            
            surviving_targets[sym] = tp
        
        if dropped_notional <= CONVERGENCE_THRESHOLD:
            result.targets = surviving_targets
            break
        
        if not surviving_targets:
            logger.warning("sizing: all targets dropped during redistribution")
            result.targets = {}
            break
        
        total_surviving_weight = sum(abs(tp.weight) for tp in surviving_targets.values())
        if total_surviving_weight > 0:
            for sym, tp in surviving_targets.items():
                weight_share = abs(tp.weight) / total_surviving_weight
                tp.target_notional += dropped_notional * weight_share
                
                price = _get_price(sym)
                if price > 0:
                    tp.target_qty = ExchangeInfoCache.round_quantity(sym, tp.target_notional / price)
        
        result.targets = surviving_targets
        logger.debug("redist iter %d: redistributed %.2f USDT to %d survivors",
                   iteration, dropped_notional, len(surviving_targets))

    # Step 7: Strongest-signal fallback if no targets
    if not result.targets:
        logger.warning("sizing: attempting strongest-signal fallback")
        
        strongest_sym = None
        strongest_score = 0.0
        for sym, snap in snapshots.items():
            if abs(snap.final_score) > abs(strongest_score):
                strongest_sym = sym
                strongest_score = snap.final_score
        
        if strongest_sym:
            filters = ExchangeInfoCache.get_symbol_filters(strongest_sym)
            min_notional = filters.get("min_notional", 5.0)
            
            fallback_notional = max(min_notional * 1.1, equity * 0.05)
            max_notional_cap = equity * leverage_target
            fallback_notional = min(fallback_notional, max_notional_cap)
            
            price = _get_price(strongest_sym)
            if price > 0:
                qty = fallback_notional / price
                qty = ExchangeInfoCache.round_quantity(strongest_sym, qty)
                
                if qty * price >= min_notional:
                    side = "LONG" if strongest_score > 0 else "SHORT"
                    snap = snapshots.get(strongest_sym)
                    tp = TargetPosition(
                        symbol=strongest_sym,
                        side=side,
                        weight=math.copysign(1.0, strongest_score),
                        target_qty=qty,
                        target_notional=qty * price,
                        trend_score=strongest_score,
                    )
                    result.targets[strongest_sym] = tp
                    result.gross_notional = qty * price
                    logger.info("sizing: fallback to %s with notional %.2f", strongest_sym, qty * price)
                else:
                    result.drop_reason = "fallback_below_min_notional"
                    logger.warning("sizing: fallback failed - qty*price %.2f < min %.2f", 
                                 qty * price, min_notional)
            else:
                result.drop_reason = "fallback_no_price"
        else:
            result.drop_reason = "no_signals_for_fallback"

    # Step 8: Final leverage constraint validation
    if result.targets:
        total_notional = sum(tp.target_notional for tp in result.targets.values())
        max_allowed = equity * leverage_target
        
        if total_notional > max_allowed:
            logger.warning("sizing: total notional %.2f exceeds max %.2f, scaling down",
                         total_notional, max_allowed)
            scale = max_allowed / total_notional
            for tp in result.targets.values():
                tp.target_notional *= scale
                price = _get_price(tp.symbol)
                if price > 0:
                    tp.target_qty = ExchangeInfoCache.round_quantity(tp.symbol, tp.target_notional / price)
            
            result.gross_notional = sum(tp.target_notional for tp in result.targets.values())

    if not result.targets and not result.drop_reason:
        result.drop_reason = "all_filtered_unknown"

    return result
