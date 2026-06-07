"""
Modular execution engine: ONLY module that calls Binance API.
Binance USDT-M Futures: isolated margin, leverage, market entry, SL/TP brackets.
Responsibilities:
1) Set isolated margin
2) Set leverage
3) Place MARKET entry order
4) Attach STOP_MARKET stop-loss (reduceOnly=true)
5) Attach take-profits: 50% at 1R, 25% at 2R, 25% trailing (ATR-based)
Safety: retry+exception handling, sequential execution, cancel on entry fail,
        add stop if position exists without one, confirm position after order.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

import numpy as np

from app.engine.modular.config_model import ModularConfig
from app.engine.modular.types import (
    ExecutionResult,
    MarketSnapshot,
    OrderPlan,
    RiskResult,
    TargetPosition,
    TradeRecord,
)

logger = logging.getLogger(__name__)

# Binance client — ONLY import in this module
from app.services.binance_client import binance_client
from app.services.exchange_info import ExchangeInfoCache

EXCLUDE_ALT_ONLY = frozenset({"BTCUSDT", "ETHUSDT"})


def fetch_trade_history(limit: int = 500) -> list[TradeRecord]:
    """
    Fetch realized PnL trades from Binance income history.
    Converts to TradeRecord for optimizer. MAE/MFE/holding_time = 0 (not available from API).
    """
    records: list[TradeRecord] = []
    if not binance_client.is_configured():
        return records
    try:
        income = _retry_exchange(
            lambda: binance_client.income_history(income_type="REALIZED_PNL", limit=limit),
            "income_history",
        )
        for item in income:
            inc = float(item.get("income", 0) or 0)
            if inc == 0:
                continue
            ts_ms = int(item.get("time", 0) or 0)
            records.append(
                TradeRecord(
                    pnl=inc,
                    win=inc > 0,
                    mae=0.0,
                    mfe=0.0,
                    holding_time_sec=0.0,
                    symbol=str(item.get("symbol", "")),
                    ts=ts_ms / 1000.0 if ts_ms else 0,
                )
            )
        records.sort(key=lambda r: r.ts, reverse=False)
    except Exception as exc:
        logger.warning("fetch_trade_history: %s", exc)
    return records

# Retry config
RETRY_COUNT = 3
RETRY_DELAY_SEC = 1.0

T = TypeVar("T")


def _retry_exchange(fn: Callable[[], T], label: str = "") -> T:
    """Wrap exchange call with retries. Never send multiple orders simultaneously."""
    last: Optional[Exception] = None
    for attempt in range(RETRY_COUNT):
        try:
            return fn()
        except Exception as exc:
            last = exc
            logger.warning("exchange %s attempt %d/%d: %s", label or fn.__name__, attempt + 1, RETRY_COUNT, exc)
            if attempt < RETRY_COUNT - 1:
                time.sleep(RETRY_DELAY_SEC)
    raise last


def _compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, window: int) -> float:
    if len(highs) < window + 1:
        return 0.0
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    return float(np.mean(tr[-window:]))


def _compute_realized_vol(closes: np.ndarray, window: int) -> float:
    if len(closes) < window + 1:
        window = max(1, len(closes) - 1)
    recent = closes[-window - 1:]
    log_ret = np.diff(np.log(recent))
    if len(log_ret) == 0:
        return 0.0
    daily_vol = float(np.std(log_ret, ddof=1))
    return daily_vol * math.sqrt(365)


def fetch_market_snapshot(config: ModularConfig) -> MarketSnapshot:
    """
    Fetch all market and account data from Binance. Single point of Binance reads.
    """
    symbols: list[str] = []
    closes_map: dict[str, np.ndarray] = {}
    vol_map: dict[str, float] = {}
    atr_map: dict[str, float] = {}
    funding_map: dict[str, float] = {}
    price_map: dict[str, float] = {}
    penalty_map: dict[str, float] = {}
    filters_map: dict[str, dict[str, Any]] = {}
    hlc_map: dict[str, dict[str, np.ndarray]] = {}
    closes_4h_map: dict[str, np.ndarray] = {}
    closes_15m_map: dict[str, np.ndarray] = {}
    hlc_15m_map: dict[str, dict[str, np.ndarray]] = {}
    closes_5m_map: dict[str, np.ndarray] = {}
    volume_5m_map: dict[str, np.ndarray] = {}
    positions: list[dict[str, Any]] = []
    equity = 0.0
    available_balance = 0.0

    if not binance_client.is_configured():
        return MarketSnapshot(
            symbols=[],
            closes_map={},
            vol_map={},
            atr_map={},
            funding_map={},
            price_map={},
            positions=[],
            equity=0.0,
            available_balance=0.0,
            penalty_map={},
            filters_map={},
        )

    try:
        info = ExchangeInfoCache.get()
        now_ms = int(time.time() * 1000)
        min_onboard_ms = now_ms - config.listing_age_days * 86_400_000

        candidates: list[str] = []
        for sym_info in info.get("symbols", []):
            if sym_info.get("contractType") != "PERPETUAL":
                continue
            if sym_info.get("quoteAsset") != "USDT":
                continue
            if sym_info.get("status") != "TRADING":
                continue
            onboard = sym_info.get("onboardDate", 0)
            if onboard and onboard > min_onboard_ms:
                continue
            candidates.append(sym_info["symbol"])

        if not candidates:
            return MarketSnapshot(
                symbols=[], closes_map={}, vol_map={}, atr_map={}, funding_map={},
                price_map={}, positions=[], equity=0.0, available_balance=0.0,
                penalty_map={}, filters_map={},
            )

        tickers = binance_client.ticker_24hr()
        vol_map_raw: dict[str, float] = {}
        if isinstance(tickers, list):
            for t in tickers:
                vol_map_raw[t.get("symbol", "")] = float(t.get("quoteVolume", 0) or 0)

        ranked = sorted(
            [(s, vol_map_raw.get(s, 0.0)) for s in candidates],
            key=lambda x: x[1],
            reverse=True,
        )
        if config.universe_mode == "alt_only":
            ranked = [(s, v) for s, v in ranked if s not in EXCLUDE_ALT_ONLY]
        top_symbols = [r[0] for r in ranked[: config.universe_top_n]]
        max_vol = ranked[0][1] if ranked else 1.0

        for sym in top_symbols:
            try:
                bt = binance_client.book_ticker(sym)
                bid = float(bt.get("bidPrice", 0) or 0)
                ask = float(bt.get("askPrice", 0) or 0)
                mid = (bid + ask) / 2.0 if (bid + ask) > 0 else 1.0
                spread = (ask - bid) / mid * 100.0 if mid > 0 else 999.0
                price_map[sym] = mid
            except Exception:
                spread = 999.0
                price_map[sym] = 0.0
            if spread > config.max_spread_pct:
                continue

            vol24 = vol_map_raw.get(sym, 0.0)
            vol_ratio = (vol24 / max_vol) if max_vol > 0 else 1.0
            spread_ratio = spread / config.max_spread_pct if config.max_spread_pct > 0 else 0.0
            penalty_map[sym] = max(0.1, vol_ratio * (1.0 - 0.5 * spread_ratio))
            symbols.append(sym)

        # Fetch klines, funding
        max_horizon = max(config.horizons) if config.horizons else 365
        bpd = 1.0 if config.signal_tf == "1d" else 6.0
        limit = min(int(max_horizon * bpd) + 10, 1500)

        for sym in symbols:
            try:
                raw = binance_client.klines(symbol=sym, interval=config.signal_tf, limit=limit)
                if not raw:
                    continue
                closes = np.array([float(k[4]) for k in raw], dtype=np.float64)
                closes = closes[closes > 0]
                if len(closes) < 30:
                    continue
                closes_map[sym] = closes
                vol_map[sym] = _compute_realized_vol(closes, config.vol_window)

                h_raw = np.array([float(k[2]) for k in raw], dtype=np.float64)
                l_raw = np.array([float(k[3]) for k in raw], dtype=np.float64)
                hlc_map[sym] = {"high": h_raw, "low": l_raw, "close": closes}
                atr_map[sym] = _compute_atr(h_raw, l_raw, closes, config.stop_atr_window)

                fr = binance_client.funding_rate(symbol=sym, limit=1)
                funding_map[sym] = float(fr[0].get("fundingRate", 0) or 0) if fr else 0.0

                # 5m for ALT mean-reversion (all symbols)
                raw_5m = binance_client.klines(symbol=sym, interval="5m", limit=100)
                if raw_5m and len(raw_5m) >= 25:
                    closes_5m = np.array([float(k[4]) for k in raw_5m], dtype=np.float64)
                    vol_5m = np.array([float(k[5]) for k in raw_5m], dtype=np.float64)
                    closes_5m_map[sym] = closes_5m
                    volume_5m_map[sym] = vol_5m

                # 4H and 15m for BTC trend following
                if sym == "BTCUSDT":
                    raw_4h = binance_client.klines(symbol=sym, interval="4h", limit=300)
                    if raw_4h and len(raw_4h) >= 201:
                        c4 = np.array([float(k[4]) for k in raw_4h], dtype=np.float64)
                        closes_4h_map[sym] = c4
                    raw_15m = binance_client.klines(symbol=sym, interval="15m", limit=300)
                    if raw_15m and len(raw_15m) >= 51:
                        h15 = np.array([float(k[2]) for k in raw_15m], dtype=np.float64)
                        l15 = np.array([float(k[3]) for k in raw_15m], dtype=np.float64)
                        c15 = np.array([float(k[4]) for k in raw_15m], dtype=np.float64)
                        closes_15m_map[sym] = c15
                        hlc_15m_map[sym] = {"high": h15, "low": l15, "close": c15}
            except Exception as exc:
                logger.debug("fetch_market_snapshot %s: %s", sym, exc)

        # Always fetch BTC 4H for dominance filter (even when alt_only excludes BTC)
        if symbols and "BTCUSDT" not in closes_4h_map:
            try:
                raw_4h = binance_client.klines(symbol="BTCUSDT", interval="4h", limit=300)
                if raw_4h and len(raw_4h) >= 201:
                    closes_4h_map["BTCUSDT"] = np.array(
                        [float(k[4]) for k in raw_4h], dtype=np.float64
                    )
            except Exception as exc:
                logger.debug("fetch_market_snapshot BTCUSDT 4h: %s", exc)

        # Account
        acct = binance_client.account()
        equity = float(acct.get("totalWalletBalance", 0) or 0)
        available_balance = float(acct.get("availableBalance", 0) or 0)
        positions = [
            p for p in binance_client.position_information()
            if float(p.get("positionAmt", 0)) != 0
        ]

        # Filters
        for sym_info in info.get("symbols", []):
            s = sym_info.get("symbol", "")
            if s not in symbols:
                continue
            flt: dict[str, Any] = {}
            for f in sym_info.get("filters", []):
                if f.get("filterType") == "PRICE_FILTER":
                    flt["tick_size"] = float(f.get("tickSize", 0.01))
                elif f.get("filterType") == "LOT_SIZE":
                    flt["step_size"] = float(f.get("stepSize", 0.001))
                elif f.get("filterType") == "MIN_NOTIONAL":
                    flt["min_notional"] = float(f.get("notional", 0))
            filters_map[s] = flt

    except Exception as exc:
        logger.error("fetch_market_snapshot failed: %s", exc)

    return MarketSnapshot(
        symbols=symbols,
        closes_map=closes_map,
        vol_map=vol_map,
        atr_map=atr_map,
        funding_map=funding_map,
        price_map=price_map,
        positions=positions,
        equity=equity,
        available_balance=available_balance,
        penalty_map=penalty_map,
        filters_map=filters_map,
        hlc_map=hlc_map,
        closes_4h_map=closes_4h_map,
        closes_15m_map=closes_15m_map,
        hlc_15m_map=hlc_15m_map,
        closes_5m_map=closes_5m_map,
        volume_5m_map=volume_5m_map,
    )


def execute_plan(
    plan: OrderPlan,
    config: ModularConfig,
    price_map: Optional[dict[str, float]] = None,
) -> ExecutionResult:
    """
    Execute OrderPlan: isolated margin, leverage, market orders.
    Delegates bracket attachment to execute_risk_plan when invoked from risk flow.
    """
    result = ExecutionResult(success=False)
    if not plan.targets or not binance_client.is_configured():
        result.errors.append("no_targets_or_not_configured")
        return result

    try:
        current_map: dict[str, dict[str, Any]] = {}
        for p in _retry_exchange(binance_client.position_information, "position_information"):
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            sym = p.get("symbol", "")
            current_map[sym] = {
                "side": "LONG" if amt > 0 else "SHORT",
                "qty": abs(amt),
                "entry_price": float(p.get("entryPrice", 0)),
            }

        threshold_notional = plan.equity * config.execution_threshold_pct
        for sym, target in plan.targets.items():
            try:
                _retry_exchange(lambda s=sym: binance_client.set_margin_type(s, "ISOLATED"), f"set_margin_{sym}")
            except Exception:
                pass
            try:
                _retry_exchange(
                    lambda s=sym, l=target.computed_leverage: binance_client.set_leverage(s, l),
                    f"set_leverage_{sym}",
                )
            except Exception:
                pass

            current = current_map.get(sym)
            current_qty = 0.0
            current_side: Optional[str] = None
            if current:
                current_qty = current["qty"]
                current_side = current["side"]

            target_signed = target.target_qty if target.side == "LONG" else -target.target_qty
            current_signed = (
                current_qty if current_side == "LONG"
                else -current_qty if current_side
                else 0.0
            )
            delta = target_signed - current_signed
            price = (price_map or {}).get(sym, 0.0)
            if abs(delta) * (price if price > 0 else 1.0) < threshold_notional:
                continue

            if delta > 0:
                order_side = "BUY"
                order_qty = abs(delta)
            else:
                order_side = "SELL"
                order_qty = abs(delta)

            order_qty = ExchangeInfoCache.round_quantity(sym, order_qty)
            if order_qty <= 0:
                continue

            reduce_only = False
            if current_side:
                if (current_side == "LONG" and order_side == "SELL" and order_qty <= current_qty):
                    reduce_only = True
                elif (current_side == "SHORT" and order_side == "BUY" and order_qty <= current_qty):
                    reduce_only = True

            cid = f"mod_{uuid.uuid4().hex[:12]}"
            try:
                _retry_exchange(
                    lambda: binance_client.new_order(
                        symbol=sym,
                        side=order_side,
                        order_type="MARKET",
                        quantity=order_qty,
                        reduce_only=reduce_only if reduce_only else None,
                        new_client_order_id=cid,
                    ),
                    f"new_order_{sym}",
                )
                result.order_ids.append(cid)
                result.success = True
                result.activity.append({"type": "order", "symbol": sym, "side": order_side, "qty": order_qty})
            except Exception as exc:
                result.errors.append(f"{sym}: {exc}")
                result.activity.append({"type": "error", "symbol": sym, "message": str(exc)})
                _cancel_all_orders_symbol(sym)

    except Exception as exc:
        result.errors.append(str(exc))

    return result


def _cancel_all_orders_symbol(symbol: str) -> None:
    """Cancel all open and algo orders for symbol (safety: entry failed)."""
    try:
        _retry_exchange(lambda: binance_client.cancel_all_open_orders(symbol), f"cancel_orders_{symbol}")
    except Exception as exc:
        logger.warning("cancel open orders %s: %s", symbol, exc)
    try:
        _retry_exchange(lambda: binance_client.cancel_all_algo_orders(symbol), f"cancel_algo_{symbol}")
    except Exception as exc:
        logger.warning("cancel algo orders %s: %s", symbol, exc)


def _position_has_stop(symbol: str, is_long: bool) -> bool:
    """Check if position has a STOP_MARKET reduce-only order for the close side."""
    try:
        algos = _retry_exchange(lambda: binance_client.get_open_algo_orders(symbol), f"open_algo_{symbol}")
        stop_side = "SELL" if is_long else "BUY"
        for a in algos:
            if a.get("type") == "STOP_MARKET" and a.get("side") == stop_side and a.get("reduceOnly") in (True, "true"):
                return True
        return False
    except Exception:
        return False


def _ensure_stop_for_position(
    symbol: str,
    amt: float,
    entry: float,
    atr: float,
    config: ModularConfig,
    risk_decisions: dict[str, Any],
) -> None:
    """If position exists without stop, add STOP_MARKET reduceOnly."""
    is_long = amt > 0
    if _position_has_stop(symbol, is_long):
        return
    stop_side = "SELL" if is_long else "BUY"
    dec = risk_decisions.get(symbol)
    atr_mult = getattr(config, "atr_stop_mult", 1.5) if (dec and getattr(dec, "stop_price", 0) > 0) else config.chandelier_atr_mult
    sl_dist = atr * atr_mult
    if is_long:
        sl_price = max(0.0001, entry - sl_dist)
    else:
        sl_price = max(0.0001, entry + sl_dist)
    qty = ExchangeInfoCache.round_quantity(symbol, abs(amt))
    _retry_exchange(
        lambda: binance_client.new_algo_order(
            symbol=symbol,
            side=stop_side,
            order_type="STOP_MARKET",
            trigger_price=sl_price,
            quantity=qty,
            reduce_only=True,
            client_algo_id=f"mod_sl_{uuid.uuid4().hex[:8]}",
        ),
        f"add_stop_{symbol}",
    )
    logger.info("Added missing stop for %s at %s", symbol, sl_price)


def risk_to_plan(risk: RiskResult, snapshot: MarketSnapshot) -> OrderPlan:
    """Convert RiskResult to OrderPlan for execution."""
    targets = {}
    for sym, dec in risk.decisions.items():
        if not dec.allowed or dec.position_size <= 0:
            continue
        targets[sym] = TargetPosition(
            symbol=sym,
            side=dec.side,
            weight=dec.exposure_pct,
            target_qty=dec.position_size,
            target_notional=dec.position_size * (snapshot.price_map.get(sym) or 0),
            computed_leverage=dec.leverage,
            stop_price=dec.stop_price or 0,
        )
    return OrderPlan(
        targets=targets,
        equity=snapshot.equity,
        gross_notional=sum(t.target_notional for t in targets.values()),
    )


def execute_risk_plan(
    risk: RiskResult,
    snapshot: MarketSnapshot,
    config: ModularConfig,
    atr_map: dict[str, float] | None = None,
) -> ExecutionResult:
    """
    Execute RiskResult for Binance USDT-M Futures.
    1) Isolated margin, 2) Leverage, 3) MARKET entry
    4) STOP_MARKET SL (reduceOnly), 5) TPs: 50% at 1R, 25% at 2R, 25% trailing (ATR-based).
    Sequential execution only. Retry all exchange calls. On entry fail → cancel all orders.
    If position exists without stop → add stop immediately.
    """
    plan = risk_to_plan(risk, snapshot)
    atr_map = atr_map or snapshot.atr_map or {}
    risk_decisions = risk.decisions
    res = ExecutionResult(success=False)

    if not plan.targets or not binance_client.is_configured():
        res.errors.append("no_targets_or_not_configured")
        return res

    # Ensure isolated margin for all targets (sequential)
    for sym in plan.targets:
        try:
            _retry_exchange(lambda s=sym: binance_client.set_margin_type(s, "ISOLATED"), f"margin_{sym}")
        except Exception as exc:
            if "-4046" not in str(exc):
                res.activity.append({"type": "margin_skip", "symbol": sym, "msg": str(exc)})

    # Execute each symbol sequentially (never simultaneously)
    for sym, target in plan.targets.items():
        try:
            _execute_one_symbol(
                sym=sym,
                target=target,
                snapshot=snapshot,
                config=config,
                plan=plan,
                atr_map=atr_map,
                risk_decisions=risk_decisions,
                res=res,
            )
        except Exception as exc:
            res.errors.append(f"{sym}: {exc}")
            res.activity.append({"type": "error", "symbol": sym, "message": str(exc)})
            _cancel_all_orders_symbol(sym)

    # Safety: ensure any open position has stop (positions from prior runs or partial fills)
    try:
        positions = _retry_exchange(binance_client.position_information, "position_information")
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            s = p.get("symbol", "")
            entry = float(p.get("entryPrice", 0))
            atr_val = atr_map.get(s, entry * 0.02)
            _ensure_stop_for_position(s, amt, entry, atr_val, config, risk_decisions)
    except Exception as exc:
        res.errors.append(f"ensure_stop: {exc}")

    res.report = {
        "symbols_executed": list(plan.targets.keys()),
        "orders_placed": len(res.order_ids),
        "success": res.success,
        "errors": list(res.errors),
        "activity_count": len(res.activity),
    }
    return res


def _execute_one_symbol(
    sym: str,
    target: TargetPosition,
    snapshot: MarketSnapshot,
    config: ModularConfig,
    plan: OrderPlan,
    atr_map: dict[str, float],
    risk_decisions: dict[str, Any],
    res: ExecutionResult,
) -> None:
    """Execute one symbol: leverage → entry → confirm → SL → TP1 → TP2 → trailing."""
    price_map = snapshot.price_map or {}
    current_map: dict[str, dict[str, Any]] = {}
    for p in _retry_exchange(binance_client.position_information, f"positions_{sym}"):
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        s = p.get("symbol", "")
        current_map[s] = {
            "side": "LONG" if amt > 0 else "SHORT",
            "qty": abs(amt),
            "entry_price": float(p.get("entryPrice", 0)),
        }

    # 2) Set leverage
    _retry_exchange(lambda: binance_client.set_leverage(sym, target.computed_leverage), f"leverage_{sym}")

    current = current_map.get(sym)
    target_signed = target.target_qty if target.side == "LONG" else -target.target_qty
    current_signed = (
        current["qty"] if current and current["side"] == "LONG"
        else -current["qty"] if current
        else 0.0
    )
    delta = target_signed - current_signed
    price = price_map.get(sym, 0.0)
    threshold = plan.equity * config.execution_threshold_pct
    if abs(delta) * (price if price > 0 else 1.0) < threshold:
        return

    order_side = "BUY" if delta > 0 else "SELL"
    order_qty = ExchangeInfoCache.round_quantity(sym, abs(delta))
    if order_qty <= 0:
        return

    reduce_only = False
    if current:
        if current["side"] == "LONG" and order_side == "SELL" and order_qty <= current["qty"]:
            reduce_only = True
        elif current["side"] == "SHORT" and order_side == "BUY" and order_qty <= current["qty"]:
            reduce_only = True

    # 3) Place MARKET entry
    cid = f"mod_{uuid.uuid4().hex[:12]}"
    try:
        _retry_exchange(
            lambda: binance_client.new_order(
                symbol=sym,
                side=order_side,
                order_type="MARKET",
                quantity=order_qty,
                reduce_only=reduce_only if reduce_only else None,
                new_client_order_id=cid,
            ),
            f"entry_{sym}",
        )
    except Exception:
        _cancel_all_orders_symbol(sym)
        raise

    res.order_ids.append(cid)
    res.success = True
    res.activity.append({"type": "order", "symbol": sym, "side": order_side, "qty": order_qty})

    # Confirm position
    time.sleep(0.5)
    pos = None
    for p in _retry_exchange(lambda: binance_client.position_information(symbol=sym), f"confirm_{sym}"):
        if float(p.get("positionAmt", 0)) != 0:
            pos = p
            break
    if not pos and not reduce_only:
        res.activity.append({"type": "confirm_miss", "symbol": sym})
        return

    # For reduce-only we don't add brackets
    if reduce_only:
        return

    amt = float(pos.get("positionAmt", 0))
    entry = float(pos.get("entryPrice", 0))
    qty = abs(amt)
    stop_side = "SELL" if amt > 0 else "BUY"
    atr_val = atr_map.get(sym, entry * 0.02)
    dec = risk_decisions.get(sym)
    atr_mult = getattr(config, "atr_stop_mult", 1.5) if (dec and getattr(dec, "stop_price", 0) > 0) else config.chandelier_atr_mult
    sl_dist = atr_val * atr_mult

    if amt > 0:
        sl_price = max(0.0001, entry - sl_dist)
    else:
        sl_price = max(0.0001, entry + sl_dist)

    # 4) STOP_MARKET SL (reduceOnly)
    _retry_exchange(
        lambda: binance_client.new_algo_order(
            symbol=sym,
            side=stop_side,
            order_type="STOP_MARKET",
            trigger_price=sl_price,
            quantity=qty,
            reduce_only=True,
            client_algo_id=f"mod_sl_{uuid.uuid4().hex[:8]}",
        ),
        f"sl_{sym}",
    )
    res.activity.append({"type": "sl", "symbol": sym, "price": sl_price})

    # 5) Take-profits: 50% at 1R, 25% at 2R, 25% trailing
    tp1_pct, tp2_pct, trail_pct = 0.50, 0.25, 0.25
    tp1_qty = ExchangeInfoCache.round_quantity(sym, qty * tp1_pct)
    tp2_qty = ExchangeInfoCache.round_quantity(sym, qty * tp2_pct)
    trail_qty = ExchangeInfoCache.round_quantity(sym, qty * trail_pct)

    if amt > 0:
        tp1_price = entry + sl_dist * config.tp1_r_multiple
        tp2_price = entry + sl_dist * config.tp2_r_multiple
    else:
        tp1_price = entry - sl_dist * config.tp1_r_multiple
        tp2_price = entry - sl_dist * config.tp2_r_multiple

    if tp1_qty > 0:
        _retry_exchange(
            lambda: binance_client.new_algo_order(
                symbol=sym,
                side=stop_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=max(0.0001, tp1_price),
                quantity=tp1_qty,
                reduce_only=True,
                client_algo_id=f"mod_tp1_{uuid.uuid4().hex[:8]}",
            ),
            f"tp1_{sym}",
        )
        res.activity.append({"type": "tp1", "symbol": sym, "price": tp1_price, "pct": tp1_pct})
    if tp2_qty > 0:
        _retry_exchange(
            lambda: binance_client.new_algo_order(
                symbol=sym,
                side=stop_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=max(0.0001, tp2_price),
                quantity=tp2_qty,
                reduce_only=True,
                client_algo_id=f"mod_tp2_{uuid.uuid4().hex[:8]}",
            ),
            f"tp2_{sym}",
        )
        res.activity.append({"type": "tp2", "symbol": sym, "price": tp2_price, "pct": tp2_pct})

    # 25% trailing (ATR-based): callbackRate = ATR/price * 100 (as %)
    if trail_qty > 0 and entry > 0:
        callback_pct = max(0.1, min(10.0, (atr_val / entry) * 100))
        if amt > 0:
            activate_price = entry + atr_val * 0.5
        else:
            activate_price = entry - atr_val * 0.5
        _retry_exchange(
            lambda: binance_client.new_algo_order_trailing(
                symbol=sym,
                side=stop_side,
                quantity=trail_qty,
                callback_rate=callback_pct,
                activate_price=activate_price,
                reduce_only=True,
                client_algo_id=f"mod_trail_{uuid.uuid4().hex[:8]}",
            ),
            f"trail_{sym}",
        )
        res.activity.append({"type": "trail", "symbol": sym, "pct": trail_pct, "callback": callback_pct})
