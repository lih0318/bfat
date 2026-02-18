"""
Position sizing: volatility targeting, Top-K concentration, weight normalisation.

Pipeline:
  1. w_raw = TrendScore / vol ? liquidity_penalty ? rsi_scale ? funding_scale
  2. Dynamic Top-K selection (equity & tradability-aware)
  3. min_weight_floor / max_weight_cap
  4. Normalise ??gross_notional ??target_qty
  5. exchangeInfo stepSize / minNotional validation
  6. Tradability-aware redistribution loop
  7. Multi-candidate tradable fallback (score-ranked search)
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


def _check_tradability(
    symbol: str,
    equity: float,
    leverage_target: float,
) -> tuple:
    """Check if *symbol* can receive a minimum viable order.

    Uses per-symbol min_notional and step_size. If initial floor-rounded
    qty falls below min_notional, bumps qty up by one step_size and
    retries (step-size-aware budget retry) while respecting leverage cap.

    Returns (is_ok, reason, qty, actual_notional, price, min_notional).
    """
    price = _get_price(symbol)
    if price <= 0:
        return (False, "no_price", 0.0, 0.0, 0.0, 0.0)

    filters = ExchangeInfoCache.get_symbol_filters(symbol)
    min_notional = filters.get("min_notional", 5.0)
    step_size = filters.get("step_size", 0.001)

    # Budget: larger of min_notional*1.1 or 5% equity, capped by leverage
    budget = max(min_notional * 1.1, equity * 0.05)
    max_budget = equity * leverage_target
    budget = min(budget, max_budget)

    qty = budget / price
    qty = ExchangeInfoCache.round_quantity(symbol, qty)

    if qty <= 0:
        return (False, "qty_zero", 0.0, 0.0, price, min_notional)

    actual_notional = qty * price

    # Step-size-aware retry: if floor rounding pushed us below min_notional,
    # bump qty by one step_size and check again within leverage cap.
    if actual_notional < min_notional and step_size > 0:
        import math as _m
        ceil_qty = qty + step_size
        # Round to correct precision to avoid float drift
        precision = max(0, -int(round(_m.log10(step_size))))
        ceil_qty = round(ceil_qty, precision)
        ceil_notional = ceil_qty * price
        if ceil_notional >= min_notional and ceil_notional <= max_budget:
            qty = ceil_qty
            actual_notional = ceil_notional
            logger.debug(
                "tradability: %s step-size bump %.6f -> %.6f (notional %.2f)",
                symbol, qty - step_size, qty, actual_notional,
            )

    if actual_notional < min_notional:
        return (False, "below_min_notional", qty, actual_notional, price, min_notional)

    if actual_notional > max_budget:
        return (False, "exceeds_leverage_cap", qty, actual_notional, price, min_notional)

    return (True, "", qty, actual_notional, price, min_notional)


@dataclass
class TargetPosition:
    symbol: str
    side: str  # "LONG" or "SHORT"
    weight: float = 0.0  # normalised portfolio weight (signed)
    target_qty: float = 0.0  # absolute quantity (always positive)
    target_notional: float = 0.0
    trend_score: float = 0.0
    computed_leverage: int = 1  # per-symbol leverage determined by risk sizing


@dataclass
class SizingResult:
    targets: dict[str, TargetPosition] = field(default_factory=dict)
    equity: float = 0.0
    gross_notional: float = 0.0
    drop_reason: str = ""  # reason if targets is empty
    drop_meta: dict[str, Any] = field(default_factory=dict)  # detailed failure info


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
    # ---- Isolated risk-sizing params ----
    risk_per_trade_pct: float = 0.01,
    max_symbol_leverage: int = 20,
    min_symbol_leverage: int = 1,
    stop_k: float = 2.0,
    atr_map: dict[str, float] | None = None,
    single_position_full_equity: bool = False,
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

    # Step 2: Dynamic Top-K selection (alt mode: one position, full equity)
    effective_top_k = 1 if single_position_full_equity else top_k
    if top_k_enabled and not single_position_full_equity:
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

    # Step 4: gross_notional from vol targeting (or full equity when single-position mode)
    max_notional = equity * leverage_target
    if single_position_full_equity and len(normalised) == 1:
        gross_notional = max_notional * 0.95  # 95% of buying power for the single position
    else:
        avg_vol = 0.0
        for sym, w in normalised.items():
            sv = vol_map.get(sym, target_vol)
            avg_vol += abs(w) * sv
        if avg_vol <= 0:
            avg_vol = target_vol
        gross_notional = equity * target_vol / avg_vol
        if gross_notional > max_notional:
            gross_notional = max_notional

    result.gross_notional = gross_notional

    # Step 5: Risk-based sizing per symbol
    # Two paths:
    #   A) ATR risk sizing: stop_dist -> risk_budget -> position_size -> leverage
    #   B) Fallback vol-target sizing (legacy): gross_notional * weight
    _atr = atr_map or {}

    for sym, w in normalised.items():
        side = "LONG" if w > 0 else "SHORT"
        snap = snapshots.get(sym)
        if not snap:
            continue

        price = _get_price(sym)
        if price <= 0:
            continue

        atr_val = _atr.get(sym, 0.0)
        sym_leverage = min_symbol_leverage  # will be computed

        if single_position_full_equity and len(normalised) == 1:
            # Single position: use full gross_notional (already set to 95% of max buying power)
            notional = gross_notional * abs(w)
            sym_leverage = max(min_symbol_leverage, min(max_symbol_leverage, int(math.ceil(leverage_target))))
        elif atr_val > 0 and risk_per_trade_pct > 0:
            # Path A: ATR-based risk sizing
            stop_dist = atr_val * stop_k
            # risk_budget = equity * risk_per_trade_pct * weight_share
            risk_budget = equity * risk_per_trade_pct * abs(w)
            if stop_dist > 0:
                # position_size (qty) from: qty * stop_dist = risk_budget
                risk_qty = risk_budget / stop_dist
                risk_notional = risk_qty * price
                # Needed leverage = notional / (equity * weight_share_of_margin)
                margin_share = equity * abs(w) * 0.90  # 10% reserve buffer
                needed_lev = risk_notional / max(margin_share, 1.0) if margin_share > 0 else 1
                sym_leverage = max(min_symbol_leverage, min(max_symbol_leverage, int(math.ceil(needed_lev))))
                notional = min(risk_notional, gross_notional * abs(w))
            else:
                notional = gross_notional * abs(w)
        else:
            # Path B: vol-target fallback
            notional = gross_notional * abs(w)
            sym_leverage = max(min_symbol_leverage, min(max_symbol_leverage, int(math.ceil(leverage_target))))

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
            target_notional=qty * price,
            trend_score=snap.final_score,
            computed_leverage=sym_leverage,
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

    # Step 7: Multi-candidate tradable fallback (score-ranked search)
    if not result.targets:
        logger.warning("sizing: attempting multi-candidate tradable fallback")

        # Sort all candidates by |final_score| descending
        candidates = sorted(
            snapshots.items(),
            key=lambda x: abs(x[1].final_score),
            reverse=True,
        )

        fail_reasons: list[str] = []
        fallback_found = False

        for sym, snap in candidates:
            if snap.final_score == 0:
                continue

            ok, reason, qty, actual_notional, price, mn = _check_tradability(
                sym, equity, leverage_target,
            )

            if ok:
                side = "LONG" if snap.final_score > 0 else "SHORT"
                tp = TargetPosition(
                    symbol=sym,
                    side=side,
                    weight=math.copysign(1.0, snap.final_score),
                    target_qty=qty,
                    target_notional=actual_notional,
                    trend_score=snap.final_score,
                )
                result.targets[sym] = tp
                result.gross_notional = actual_notional
                fallback_found = True
                logger.info(
                    "sizing: fallback OK -> %s (score=%.4f, notional=%.2f, rank=%d/%d)",
                    sym, snap.final_score, actual_notional,
                    len(fail_reasons) + 1, len(candidates),
                )
                break
            else:
                fail_reasons.append(reason)
                logger.debug(
                    "sizing: fallback skip %s reason=%s (score=%.4f, price=%.4f)",
                    sym, reason, snap.final_score, price,
                )

        if not fallback_found:
            from collections import Counter
            reason_counts = Counter(fail_reasons)
            summary_parts = [f"{cnt}x {r}" for r, cnt in reason_counts.most_common()]
            summary = ", ".join(summary_parts) if summary_parts else "no candidates"
            result.drop_reason = "fallback_all_untradable"
            result.drop_meta = {
                "tried": len(fail_reasons),
                "reason_counts": dict(reason_counts),
                "summary": summary,
            }
            logger.warning(
                "sizing: all %d fallback candidates untradable (%s)",
                len(fail_reasons), summary,
            )

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
