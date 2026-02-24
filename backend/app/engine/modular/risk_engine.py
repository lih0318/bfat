"""
Risk engine: position size, leverage, stop, trade allowed.
Inputs: account_balance, open_positions, signal_object, ATR, recent_winrate,
        current_drawdown, btc_regime.
Base risk: 0.5%. Drawdown scaling. Stop = 1.5*ATR.
Leverage: BTC 2-5x, ALT 1.5-3x. Exposure: total ≤30%, ALT ≤10%.
Block: 3 consecutive losses, abnormal vol, funding < -0.05%.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from app.engine.modular.config_model import ModularConfig
from app.engine.modular.types import (
    MarketSnapshot,
    RiskDecision,
    RiskResult,
    SignalResult,
)

logger = logging.getLogger(__name__)

# Constants from spec (overridden by config when provided)
DEFAULT_RISK_PCT = 0.005
DEFAULT_ATR_STOP = 1.5
DRAWDOWN_10_CUT = 0.5
DRAWDOWN_20_CUT = 0.25
MAX_TOTAL_EXPOSURE_PCT = 0.30
MAX_ALT_EXPOSURE_PCT = 0.10
FUNDING_BLOCK_THRESHOLD = -0.0005  # -0.05%
ATR_PERCENTILE_LEV_CUT = 80  # reduce leverage 40% if above
LEV_REDUCE_PCT = 0.40
BTC_LEV_MIN = 2
BTC_LEV_MAX = 5
ALT_LEV_MIN = 2   # 1.5x rounded up
ALT_LEV_MAX = 3
CONSEC_LOSSES_BLOCK = 3
ATR_PERCENTILE_VOL_SPIKE = 95  # block if above (abnormal vol)


@dataclass
class RiskContext:
    """Optional risk inputs. Defaults used when not provided."""
    recent_winrate: float = 0.5
    current_drawdown_pct: float = 0.0
    btc_regime: str = "neutral"
    consecutive_losses: int = 0
    atr_percentile_map: dict[str, float] = field(default_factory=dict)


def _round_quantity(step_size: float, qty: float) -> float:
    if step_size <= 0:
        return qty
    prec = max(0, -int(round(math.log10(step_size))))
    return round(qty - (qty % step_size), prec)


def _is_btc(symbol: str) -> bool:
    return symbol == "BTCUSDT"


def _compute_leverage(
    symbol: str,
    atr_pct: Optional[float],
) -> tuple[int, int]:
    """Return (min_lev, max_lev) for symbol. Reduce max if ATR percentile > 80."""
    if _is_btc(symbol):
        base_min, base_max = BTC_LEV_MIN, BTC_LEV_MAX
    else:
        base_min, base_max = ALT_LEV_MIN, ALT_LEV_MAX

    if atr_pct is not None and atr_pct > ATR_PERCENTILE_LEV_CUT:
        max_lev = max(base_min, int((base_max - base_min) * (1 - LEV_REDUCE_PCT) + base_min))
        return base_min, min(base_max, max_lev)
    return base_min, base_max


def run(
    signals: SignalResult,
    snapshot: MarketSnapshot,
    config: ModularConfig,
    context: Optional[RiskContext] = None,
    current_positions: Optional[dict[str, float]] = None,
) -> RiskResult:
    """
    Compute risk per symbol: allow_trade, position_size, leverage, stop_price.
    Uses: equity, positions, signal outputs, ATR, drawdown, consecutive losses.
    """
    result = RiskResult()
    if snapshot.equity <= 0:
        return result

    ctx = context or RiskContext()
    equity = snapshot.equity
    price_map = snapshot.price_map or {}
    atr_map = snapshot.atr_map or {}
    funding_map = snapshot.funding_map or {}
    filters_map = snapshot.filters_map or {}

    # Drawdown scaling
    risk_mult = 1.0
    if ctx.current_drawdown_pct > 20:
        risk_mult = DRAWDOWN_20_CUT
    elif ctx.current_drawdown_pct > 10:
        risk_mult = DRAWDOWN_10_CUT

    # Block conditions (global)
    block_consec = ctx.consecutive_losses >= CONSEC_LOSSES_BLOCK

    total_exposure = 0.0
    alt_exposure = 0.0

    # Build signal source: prefer outputs, fallback to snapshots
    for sym, out in (signals.outputs or signals.snapshots).items():
        direction = getattr(out, "direction", None)
        confidence = getattr(out, "confidence", 0.5)
        market_regime = getattr(out, "market_regime", "neutral")
        final_score = getattr(out, "final_score", 0.0)

        if direction is None:
            direction = "LONG" if final_score > 0 else "SHORT" if final_score < 0 else "NONE"
        if direction == "NONE":
            continue

        price = price_map.get(sym, 0.0)
        if price <= 0:
            result.decisions[sym] = RiskDecision(
                symbol=sym, allowed=False, position_size=0, leverage=1,
                exposure_pct=0, reason="no_price", side=direction,
            )
            continue

        # Per-symbol block checks
        funding = funding_map.get(sym, 0.0)
        block_funding = funding < FUNDING_BLOCK_THRESHOLD
        atr_pct = ctx.atr_percentile_map.get(sym)
        block_vol = atr_pct is not None and atr_pct > ATR_PERCENTILE_VOL_SPIKE

        allowed = not (block_consec or block_funding)
        reason = ""
        if block_consec:
            reason = "3_consecutive_losses"
        elif block_funding:
            reason = "funding_rate_block"
        elif block_vol:
            reason = "abnormal_volatility"

        atr_val = atr_map.get(sym, price * 0.02)
        stop_mult = getattr(config, "atr_stop_mult", None) or DEFAULT_ATR_STOP
        stop_dist = stop_mult * atr_val
        if stop_dist <= 0:
            stop_dist = price * 0.03

        # Risk sizing
        conf_mult = 0.5 + confidence
        risk_pct = getattr(config, "risk_per_trade_pct", None) or DEFAULT_RISK_PCT
        base_risk = equity * risk_pct * conf_mult * risk_mult
        position_size = base_risk / stop_dist if stop_dist > 0 else 0.0

        # Cap by exposure limits
        notional = position_size * price
        exposure_pct = notional / equity if equity > 0 else 0
        lev_min, lev_max = _compute_leverage(sym, atr_pct)

        # Check exposure before allowing
        new_total = total_exposure + exposure_pct
        new_alt = alt_exposure + (0 if _is_btc(sym) else exposure_pct)
        if new_total > MAX_TOTAL_EXPOSURE_PCT:
            allowed = False
            reason = reason or "total_exposure_limit"
        if new_alt > MAX_ALT_EXPOSURE_PCT and not _is_btc(sym):
            allowed = False
            reason = reason or "alt_exposure_limit"

        # Leverage: minimum needed so margin = notional/lev fits, clamped to [min,max]
        target_lev = lev_min
        if equity > 0 and notional > 0:
            needed = max(1, int(round(notional / equity)))
            target_lev = max(lev_min, min(lev_max, needed))

        # Round position
        step_size = filters_map.get(sym, {}).get("step_size", 0.001)
        min_notional = filters_map.get(sym, {}).get("min_notional", 5.0)
        qty = _round_quantity(step_size, position_size)
        if qty * price < min_notional:
            allowed = False
            reason = reason or "below_min_notional"
            qty = 0.0

        # Stop price
        stop_price = 0.0
        if qty > 0 and atr_val > 0:
            mult = getattr(config, "atr_stop_mult", None) or DEFAULT_ATR_STOP
            if direction == "LONG":
                stop_price = max(0.0001, price - mult * atr_val)
            else:
                stop_price = max(0.0001, price + mult * atr_val)

        if allowed:
            total_exposure += exposure_pct
            if not _is_btc(sym):
                alt_exposure += exposure_pct

        result.decisions[sym] = RiskDecision(
            symbol=sym,
            allowed=allowed,
            position_size=qty,
            leverage=target_lev,
            exposure_pct=exposure_pct,
            reason=reason,
            side=direction,
            stop_price=stop_price,
        )

    result.total_exposure_pct = total_exposure
    return result
