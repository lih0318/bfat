"""
Sizing engine: position sizing, leverage, weight normalisation.
Pure logic — no Binance API calls. Uses only MarketSnapshot and SignalResult.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from app.engine.modular.config_model import ModularConfig
from app.engine.modular.types import (
    MarketSnapshot,
    OrderPlan,
    SignalResult,
    TargetPosition,
)

logger = logging.getLogger(__name__)


def _round_quantity(step_size: float, qty: float) -> float:
    """Round qty to step_size precision."""
    if step_size <= 0:
        return qty
    prec = max(0, -int(round(math.log10(step_size))))
    return round(qty - (qty % step_size), prec)


def _compute_dynamic_top_k(raw_weights: dict[str, float], equity: float, top_k: int) -> int:
    """Compute dynamic top-k based on equity and tradability."""
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
    tradable_count = sum(
        1
        for _ in sorted_by_abs[:top_k]
        if equity / max(top_k, 1) >= 10.0 * 1.5
    )
    tradability_based_k = max(1, tradable_count)
    return min(equity_based_k, tradability_based_k, top_k)


def run(
    signals: SignalResult,
    snapshot: MarketSnapshot,
    config: ModularConfig,
    current_symbols: set[str] | None = None,
) -> OrderPlan:
    """
    Compute target positions. No Binance calls.
    Uses price_map and filters_map from MarketSnapshot.
    """
    result = OrderPlan(equity=snapshot.equity)
    if snapshot.equity <= 0:
        result.drop_reason = "zero_equity"
        return result

    vol_map = snapshot.vol_map or {}
    atr_map = snapshot.atr_map or {}
    penalty_map = snapshot.penalty_map or {}
    price_map = snapshot.price_map or {}
    filters_map = snapshot.filters_map or {}
    snapshots = signals.snapshots

    single_position_full_equity = config.universe_mode == "alt_only"

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

    # Step 2: Top-K
    effective_top_k = 1 if single_position_full_equity else config.top_k
    if config.top_k_enabled and not single_position_full_equity:
        effective_top_k = _compute_dynamic_top_k(raw_weights, snapshot.equity, config.top_k)

    if config.top_k_enabled and len(raw_weights) > effective_top_k:
        sorted_by_abs = sorted(raw_weights.items(), key=lambda x: abs(x[1]), reverse=True)
        current_symbols = current_symbols or set()
        selected: dict[str, float] = {}
        for sym, w in sorted_by_abs:
            if len(selected) >= effective_top_k:
                if sym in current_symbols:
                    weakest_abs = min(abs(v) for v in selected.values())
                    if abs(w) > weakest_abs:
                        weakest_sym = min(selected, key=lambda s: abs(selected[s]))
                        del selected[weakest_sym]
                        selected[sym] = w
                break
            selected[sym] = w
        raw_weights = selected

    # Step 3: normalise
    abs_sum = sum(abs(w) for w in raw_weights.values())
    if abs_sum <= 0:
        result.drop_reason = "zero_abs_sum"
        return result

    normalised: dict[str, float] = {}
    for sym, w in raw_weights.items():
        nw = w / abs_sum
        if abs(nw) < config.min_weight_floor:
            continue
        if abs(nw) > config.max_weight_cap:
            nw = math.copysign(config.max_weight_cap, nw)
        normalised[sym] = nw

    if not normalised:
        result.drop_reason = "all_below_min_weight"
        return result

    abs_sum2 = sum(abs(w) for w in normalised.values())
    if abs_sum2 > 0:
        scale = 1.0 / abs_sum2
        normalised = {s: w * scale for s, w in normalised.items()}

    # Step 4: gross_notional
    equity = snapshot.equity
    leverage_target = config.effective_leverage_target
    target_vol = config.target_portfolio_vol
    max_notional = equity * leverage_target
    conviction_scale = 1.0
    vol_scale = 1.0

    if single_position_full_equity and len(normalised) == 1:
        single_sym = next(iter(normalised))
        single_snap = snapshots.get(single_sym)
        single_vol = vol_map.get(single_sym, target_vol)
        if config.adaptive_leverage_enabled and single_snap:
            ref_score = max(config.deadzone_threshold * 3.0, 0.01)
            conviction_scale = min(1.0, max(0.5, abs(single_snap.final_score) / ref_score))
            vol_scale = min(1.0, target_vol / single_vol) if single_vol > 0 else 1.0
            concentration_pct = (
                config.min_concentration_pct
                + (config.max_concentration_pct - config.min_concentration_pct)
                * conviction_scale
                * vol_scale
            )
            concentration_pct = max(
                config.min_concentration_pct,
                min(config.max_concentration_pct, concentration_pct),
            )
            gross_notional = max_notional * concentration_pct
        else:
            gross_notional = max_notional * 0.95
    else:
        avg_vol = sum(abs(w) * vol_map.get(sym, target_vol) for sym, w in normalised.items())
        if avg_vol <= 0:
            avg_vol = target_vol
        gross_notional = equity * target_vol / avg_vol
        if gross_notional > max_notional:
            gross_notional = max_notional

    result.gross_notional = gross_notional

    # Step 5: per-symbol targets
    for sym, w in normalised.items():
        side = "LONG" if w > 0 else "SHORT"
        snap = snapshots.get(sym)
        if not snap:
            continue

        price = price_map.get(sym, 0.0)
        if price <= 0:
            continue

        filters = filters_map.get(sym, {})
        min_notional = filters.get("min_notional", 5.0)
        step_size = filters.get("step_size", 0.001)
        atr_val = atr_map.get(sym, 0.0)
        sym_leverage = config.min_symbol_leverage

        if single_position_full_equity and len(normalised) == 1:
            notional = gross_notional * abs(w)
            if config.adaptive_leverage_enabled:
                scaled_lev = leverage_target * conviction_scale * vol_scale
                sym_leverage = max(
                    config.min_symbol_leverage,
                    min(config.max_symbol_leverage, int(round(scaled_lev))),
                )
            else:
                sym_leverage = max(
                    config.min_symbol_leverage,
                    min(config.max_symbol_leverage, int(round(leverage_target))),
                )
        elif atr_val > 0 and config.risk_per_trade_pct > 0:
            stop_dist = atr_val * config.stop_k
            risk_budget = equity * config.risk_per_trade_pct * abs(w)
            if stop_dist > 0:
                risk_qty = risk_budget / stop_dist
                risk_notional = risk_qty * price
                margin_share = equity * abs(w) * 0.90
                needed_lev = risk_notional / max(margin_share, 1.0) if margin_share > 0 else 1
                sym_leverage = max(
                    config.min_symbol_leverage,
                    min(config.max_symbol_leverage, int(round(needed_lev))),
                )
                notional = min(risk_notional, gross_notional * abs(w))
            else:
                notional = gross_notional * abs(w)
        else:
            notional = gross_notional * abs(w)
            sym_leverage = max(
                config.min_symbol_leverage,
                min(config.max_symbol_leverage, int(round(leverage_target))),
            )

        qty = notional / price
        qty = _round_quantity(step_size, qty)
        if qty * price < min_notional:
            continue

        result.targets[sym] = TargetPosition(
            symbol=sym,
            side=side,
            weight=w,
            target_qty=qty,
            target_notional=qty * price,
            computed_leverage=sym_leverage,
            trend_score=snap.final_score,
        )

    # Step 6: leverage cap validation
    if result.targets:
        total_notional = sum(tp.target_notional for tp in result.targets.values())
        max_allowed = equity * leverage_target
        if total_notional > max_allowed:
            scale = max_allowed / total_notional
            for tp in result.targets.values():
                tp.target_notional *= scale
                price = price_map.get(tp.symbol, 0.0)
                if price > 0:
                    filters = filters_map.get(tp.symbol, {})
                    step = filters.get("step_size", 0.001)
                    tp.target_qty = _round_quantity(step, tp.target_notional / price)
            result.gross_notional = sum(tp.target_notional for tp in result.targets.values())

    if not result.targets and not result.drop_reason:
        result.drop_reason = "all_filtered"

    return result
