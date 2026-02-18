"""
Engine API: replaces old autopilot endpoints.
Keeps /api/autopilot/* paths for frontend compatibility.

Endpoints:
  GET  /config          → engine config (profile 포함)
  PUT  /config          → update engine config
  POST /start           → start engine
  POST /stop            → stop engine
  GET  /status          → extended status
  GET  /activity        → activity log
  GET  /market-regime   → TrendScore-based regime (1d/1h 유지)
  GET  /portfolio       → per-symbol target/current/weight/TrendScore  [NEW]
  GET  /signals         → full universe TrendScore snapshot            [NEW]
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.engine.accounting import ledger
from app.engine.config_model import EngineConfig, load_engine_config, save_engine_config
from app.engine.datafeed import fetch_atr_map, fetch_vol_map
from app.engine.profiles import PROFILES, apply_profile
from app.engine.runner import engine
from app.engine.signals import generate_reasoning
from app.services.binance_client import binance_client

router = APIRouter()


# ── Config ───────────────────────────────────────────────────────


class EngineConfigUpdate(BaseModel):
    """Partial update model — all fields optional."""
    profile: str | None = None
    signal_tf: str | None = None
    horizons: list[int] | None = None
    deadzone_threshold: float | None = None
    vol_window: int | None = None
    target_portfolio_vol: float | None = None
    effective_leverage_target: float | None = None
    stop_atr_window: int | None = None
    stop_k: float | None = None
    trailing_stop: bool | None = None
    execution_tick_sec: int | None = None
    execution_threshold_pct: float | None = None
    entry_order_mode: str | None = None
    ioc_epsilon: float | None = None
    top_k_enabled: bool | None = None
    top_k: int | None = None
    replace_threshold: float | None = None
    min_weight_floor: float | None = None
    max_weight_cap: float | None = None
    rsi_period: int | None = None
    rsi_overbought: float | None = None
    rsi_oversold: float | None = None
    rsi_scale_overbought: float | None = None
    rsi_scale_oversold: float | None = None
    funding_scale_enabled: bool | None = None
    universe_mode: str | None = None
    universe_top_n: int | None = None
    listing_age_days: int | None = None
    max_spread_pct: float | None = None
    drawdown_kill_pct: float | None = None
    margin_mode: str | None = None
    risk_per_trade_pct: float | None = None
    max_symbol_leverage: int | None = None
    min_symbol_leverage: int | None = None
    max_concurrent_symbols: int | None = None
    reserve_margin_buffer_pct: float | None = None
    chandelier_atr_mult: float | None = None
    tp1_r_multiple: float | None = None
    tp2_r_multiple: float | None = None
    tp1_close_pct: float | None = None
    tp2_close_pct: float | None = None
    breakeven_after_tp1: bool | None = None
    breakeven_offset_bps: int | None = None
    symbol: str | None = None


@router.get("/config")
def get_config() -> dict[str, Any]:
    """Get current engine configuration."""
    cfg = load_engine_config()
    return cfg.model_dump()


@router.put("/config")
def put_config(update: EngineConfigUpdate) -> dict[str, Any]:
    """Update engine configuration. If profile changes, apply preset first."""
    cfg = load_engine_config()
    d = cfg.model_dump()
    u = update.model_dump(exclude_none=True)

    # If profile is being changed, apply preset defaults first
    new_profile = u.get("profile")
    if new_profile and new_profile in PROFILES:
        apply_profile(d, new_profile)

    # Then overlay user-provided fields (may override preset)
    d.update(u)

    try:
        new_cfg = EngineConfig.model_validate(d)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    save_engine_config(new_cfg)

    # If engine is running, update its config live
    if engine.running:
        engine.config = new_cfg

    return {"ok": True, "config": new_cfg.model_dump()}


# ── Start / Stop ─────────────────────────────────────────────────


@router.post("/start")
def start_engine() -> dict[str, Any]:
    result = engine.start()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Failed"))
    return result


@router.post("/stop")
def stop_engine() -> dict[str, Any]:
    return engine.stop()


# ── Status ───────────────────────────────────────────────────────


@router.get("/status")
def status() -> dict[str, Any]:
    return engine.get_status()


# ── Activity ─────────────────────────────────────────────────────


@router.get("/activity")
def activity(
    limit: int = Query(100, ge=1, le=500),
    mode: str = Query("all", description="all | live"),
) -> list[dict[str, Any]]:
    if mode not in ("all", "live"):
        mode = "all"
    return ledger.get_activity(limit=limit, mode=mode)


# ── Market Regime (TrendScore-based) ─────────────────────────────


@router.get("/market-regime")
def market_regime(
    symbol: str | None = Query(None, description="e.g. BTCUSDT"),
) -> dict[str, Any]:
    """
    TrendScore-based regime display.
    Falls back to ADX-based regime for backward compatibility.
    Returns 1d and 1h data.
    """
    cfg = load_engine_config()
    sym = (symbol or cfg.symbol or "BTCUSDT").strip().upper()

    # Try to use engine snapshots first
    snapshots = engine.get_signals()
    engine_data = None
    for s in snapshots:
        if s.get("symbol") == sym:
            engine_data = s
            break

    if engine_data:
        # Build response from TrendScore data
        ts = engine_data.get("trend_score", 0)
        regime = "trending" if abs(ts) > 0 else "ranging"
        direction = "up" if ts > 0 else "down" if ts < 0 else "neutral"

        return {
            "symbol": sym,
            "1d": {
                "timeframe": "1d",
                "adx": None,
                "regime": regime,
                "trend_direction": direction,
                "trend_score": ts,
            },
            "1h": {
                "timeframe": "1h",
                "adx": None,
                "regime": regime,
                "trend_direction": direction,
                "trend_score": ts,
            },
        }

    # Fallback to ADX-based
    from app.services.market_regime import get_market_regime
    return get_market_regime(sym)


# ── NEW: Portfolio ───────────────────────────────────────────────


@router.get("/portfolio")
def portfolio() -> list[dict[str, Any]]:
    """Per-symbol target_qty, current_qty, weight, TrendScore."""
    return engine.get_portfolio()


# ── NEW: Signals ─────────────────────────────────────────────────


@router.get("/signals")
def signals() -> list[dict[str, Any]]:
    """Full universe TrendScore snapshot with vol/atr/reasoning."""
    signals_data = engine.get_signals()
    
    # Fetch vol/atr for all symbols
    if signals_data:
        cfg = load_engine_config()
        symbols = [s["symbol"] for s in signals_data]
        
        try:
            vol_map = fetch_vol_map(symbols, cfg.signal_tf, cfg.vol_window)
            atr_map = fetch_atr_map(symbols, cfg.signal_tf, cfg.stop_atr_window)
        except Exception:
            vol_map = {}
            atr_map = {}
        
        # Add vol/atr/reasoning to each signal
        for sig in signals_data:
            sym = sig["symbol"]
            sig["realized_vol"] = round(vol_map.get(sym, 0.0), 4)
            sig["atr"] = round(atr_map.get(sym, 0.0), 2)
            
            # Generate reasoning (reconstruct SignalSnapshot for this)
            from app.engine.signals import SignalSnapshot
            snap = SignalSnapshot(
                symbol=sym,
                trend_score_raw=sig["trend_score_raw"],
                trend_score=sig["trend_score"],
                final_score=sig["final_score"],
                rsi=sig["rsi"],
                rsi_scale=sig["rsi_scale"],
                funding_rate=sig["funding_rate"],
                funding_scale=sig["funding_scale"],
                horizon_signals=sig["horizons"],
            )
            sig["reasoning"] = generate_reasoning(snap)
    
    return signals_data


# ── NEW: Insight ─────────────────────────────────────────────────


@router.get("/insight")
def insight() -> dict[str, Any]:
    """Comprehensive engine insight data for Insight tab."""
    insight_data = engine.get_insight()
    
    # Add signals with reasoning for decision log
    signals_data = signals()  # reuse the signals endpoint
    insight_data["signals"] = signals_data
    
    # Add portfolio for decision context
    portfolio_data = engine.get_portfolio()
    insight_data["portfolio"] = portfolio_data
    
    return insight_data


# ── NEW: Bracket states (live SL/TP for Positions tab) ───────


def _bracket_fallback_from_algo_orders(
    symbol: str, entry_price: float, position_side: str
) -> dict[str, Any] | None:
    """Build bracket state from Binance open algo orders for one symbol. Returns None on error or no algos."""
    try:
        if not binance_client.is_configured():
            return None
        orders = binance_client.get_open_algo_orders(symbol)
    except Exception:
        return None
    sl_price = 0.0
    tp_prices: list[float] = []
    for o in orders:
        order_type = (o.get("orderType") or o.get("type") or "").upper()
        trigger_str = o.get("triggerPrice") or o.get("trigger_price") or "0"
        try:
            trigger = float(trigger_str)
        except (TypeError, ValueError):
            continue
        if "STOP_MARKET" in order_type or order_type == "STOP":
            sl_price = trigger
        elif "TAKE_PROFIT" in order_type or "TAKE_PROFIT_MARKET" in order_type:
            tp_prices.append(trigger)
    if sl_price <= 0 and not tp_prices:
        return None
    # Long: TP above entry, sort ascending (closer = TP1). Short: TP below entry, sort descending (closer = TP1).
    is_long = position_side and position_side.upper() in ("LONG", "BUY")
    tp_prices.sort(reverse=not is_long)
    tp1_price = float(tp_prices[0]) if len(tp_prices) > 0 else 0.0
    tp2_price = float(tp_prices[1]) if len(tp_prices) > 1 else 0.0
    return {
        "sl_price": sl_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "tp1_done": False,
        "tp2_done": False,
        "be_moved": False,
        "entry_price": entry_price,
        "initial_r": 0.0,
        "position_side": position_side,
    }


@router.get("/brackets")
def brackets() -> dict[str, Any]:
    """Return live bracket states (SL/TP1/TP2/BE) for all tracked symbols. Falls back to Binance algo orders for symbols without engine state."""
    result = dict(engine._execution.get_all_bracket_states())
    try:
        if not binance_client.is_configured():
            return result
        positions_raw = binance_client.position_information(symbol=None)
    except Exception:
        return result
    for p in positions_raw:
        amt = float(p.get("positionAmt") or p.get("position_amt") or 0)
        if amt == 0:
            continue
        symbol = (p.get("symbol") or "").strip()
        if not symbol:
            continue
        if symbol in result and ((result[symbol].get("sl_price") or 0) > 0 or (result[symbol].get("tp1_price") or 0) > 0):
            continue
        entry_price = float(p.get("entryPrice") or p.get("entry_price") or 0)
        position_side = "BUY" if amt > 0 else "SELL"
        fallback = _bracket_fallback_from_algo_orders(symbol, entry_price, position_side)
        if fallback:
            result[symbol] = fallback
    return result
