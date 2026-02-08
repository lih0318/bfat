"""
Autopilot service: config load/save, signal loop, order placement with safety rules,
activity log, daily loss limit, reentry cooldown, exchange info validation.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.models.autopilot_config import AutopilotConfig
from app.services.binance_client import binance_client
from app.services.exchange_info import ExchangeInfoCache
from app.services.journal_service import append_entry as journal_append, get_entries as journal_get_entries
from app.services.market_regime import get_market_regime
from app.strategies.base import MarketData, MarketDataCandle, SignalResult
from app.strategies.confluence_atr import ConfluenceATRStrategy
from app.strategies.range_rsi import RangeRSIStrategy

logger = logging.getLogger(__name__)

# In-memory state (for Windows Standalone: could persist to config_dir)
_autopilot_running = False
_autopilot_status: dict[str, Any] = {"running": False, "reason": ""}
_config: AutopilotConfig | None = None
_activity_log: list[dict[str, Any]] = []
_activity_max = 200
_symbol_exit_ts: dict[str, float] = {}
_symbols_with_position_last_cycle: set[str] = set()
_daily_realized_pnl: float = 0.0
_daily_reset_date: str = ""
_cycle_interval_sec = 60
_loop_thread: Any = None
_stop_loop = False


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _in_trading_hours(utc_range: str | None) -> bool:
    if not utc_range or "-" not in utc_range:
        return True
    try:
        start, end = utc_range.strip().split("-")
        sh, sm = int(start.split(":")[0]), int(start.split(":")[1]) if ":" in start else 0
        eh, em = int(end.split(":")[0]), int(end.split(":")[1]) if ":" in end else 0
        now = datetime.now(timezone.utc)
        mins = now.hour * 60 + now.minute
        start_mins = sh * 60 + sm
        end_mins = eh * 60 + em
        return start_mins <= mins <= end_mins
    except Exception:
        return True


def load_config() -> AutopilotConfig:
    global _config
    path = settings.autopilot_config_path
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _config = AutopilotConfig.model_validate(data)
            return _config
        except Exception as e:
            logger.warning("Failed to load autopilot config: %s", e)
    _config = AutopilotConfig()
    return _config


def save_config(cfg: AutopilotConfig) -> None:
    path = settings.autopilot_config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.model_dump_for_save(), f, indent=2, ensure_ascii=False)
    global _config
    _config = cfg


def get_config() -> AutopilotConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _append_activity(typ: str, symbol: str, message: str, payload: dict[str, Any] | None = None) -> None:
    global _activity_log
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": typ,
        "symbol": symbol,
        "message": message,
        **(payload or {}),
    }
    _activity_log.append(entry)
    if len(_activity_log) > _activity_max:
        _activity_log = _activity_log[-_activity_max:]


def get_activity(limit: int = 100, mode: str = "all") -> list[dict[str, Any]]:
    """mode: all | live. Live = real order events only (entry/exit/error)."""
    entries = list(reversed(_activity_log[-limit:]))
    if mode == "live":
        entries = [e for e in entries if e.get("type") in ("entry", "exit", "error")]
    return entries


def get_status() -> dict[str, Any]:
    global _autopilot_running, _autopilot_status
    cfg = get_config()
    return {
        "running": _autopilot_running,
        "reason": _autopilot_status.get("reason", ""),
        "symbol": cfg.symbol,
        "max_usdt": cfg.max_usdt,
        "max_leverage": cfg.max_leverage,
    }


def _has_position(symbol: str) -> bool:
    try:
        positions = binance_client.position_information(symbol=symbol)
        for p in positions:
            if float(p.get("positionAmt", 0)) != 0:
                return True
        return False
    except Exception:
        return True


def _get_live_position(symbol: str) -> dict[str, Any] | None:
    """Return current live position for symbol: { side, qty, entry_price, notional } or None."""
    try:
        positions = binance_client.position_information(symbol=symbol)
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            entry = float(p.get("entryPrice", 0))
            qty = abs(amt)
            side = "BUY" if amt > 0 else "SELL"
            notional = entry * qty
            return {"side": side, "qty": qty, "entry_price": entry, "notional": notional}
        return None
    except Exception:
        return None


def _apply_daily_reset() -> None:
    global _daily_realized_pnl, _daily_reset_date
    today = _today_utc()
    if today != _daily_reset_date:
        _daily_reset_date = today
        _daily_realized_pnl = 0.0


def start_autopilot() -> dict[str, Any]:
    global _autopilot_running, _autopilot_status, _symbol_exit_ts, _symbols_with_position_last_cycle
    if _autopilot_running:
        return {"ok": False, "message": "Already running"}
    if not binance_client.is_configured():
        return {"ok": False, "message": "Binance API not configured"}
    cfg = get_config()
    if cfg.daily_loss_limit_usdt > 0:
        _apply_daily_reset()
    # Fresh start: clear reentry state so first entry is allowed immediately.
    # Reentry cooldown will apply only after we detect an exit (SL/TP) during this run.
    _symbol_exit_ts.clear()
    _symbols_with_position_last_cycle.clear()
    _autopilot_running = True
    _autopilot_status = {"running": True, "reason": ""}
    _append_activity("system", cfg.symbol, "Rich Man started")
    _start_loop()
    return {"ok": True, "message": "Started"}


def stop_autopilot() -> dict[str, Any]:
    global _autopilot_running, _autopilot_status
    _autopilot_running = False
    _autopilot_status = {"running": False, "reason": "Stopped by user"}
    _stop_loop_thread()
    cfg = get_config()
    _append_activity("system", cfg.symbol, "Rich Man stopped")
    return {"ok": True, "message": "Stopped"}


def run_one_cycle() -> None:
    """Single cycle: fetch data, compute signal, check safety, place order."""
    global _autopilot_running, _autopilot_status, _daily_realized_pnl
    if not _autopilot_running:
        return
    cfg = get_config()
    symbol = cfg.symbol.upper()
    _apply_daily_reset()
    if cfg.daily_loss_limit_usdt > 0 and _daily_realized_pnl <= -cfg.daily_loss_limit_usdt:
        _autopilot_running = False
        _autopilot_status = {"running": False, "reason": "daily_limit_reached"}
        _append_activity("system", symbol, f"Rich Man stopped: daily loss limit reached ({_daily_realized_pnl:.2f} USDT)")
        return
    if not _in_trading_hours(cfg.trading_hours_utc):
        return
    try:
        lp = _get_live_position(symbol)
        has_position_now = lp is not None
        global _symbol_exit_ts, _symbols_with_position_last_cycle
        if symbol in _symbols_with_position_last_cycle and not has_position_now:
            _symbol_exit_ts[symbol] = time.time()
            _append_activity("system", symbol, "Position closed (SL/TP or manual); reentry cooldown started")
            # Fetch realized PnL from Binance and write exit journal entry (with balance %)
            try:
                # 1) Find matching entry journal record for this symbol
                entries = journal_get_entries(500, "live")
                last_entry = None
                for e in entries:
                    if e.get("type") == "entry" and e.get("symbol") == symbol:
                        last_entry = e
                        break

                # 2) Determine startTime for income lookup (entry timestamp or fallback 24h)
                entry_ts = None
                if last_entry and last_entry.get("entry_ts"):
                    entry_ts = int(last_entry["entry_ts"])
                else:
                    entry_ts = int((time.time() - 86400) * 1000)  # fallback: 24h ago

                # 3) Sum ALL REALIZED_PNL income records since entry (handles partial fills)
                income_list = binance_client.income_history(
                    symbol=symbol,
                    income_type="REALIZED_PNL",
                    start_time=entry_ts,
                    limit=100,
                )
                realized_pnl = sum(float(rec.get("income", 0) or 0) for rec in income_list) if income_list else 0.0

                # 4) Get actual exit price from userTrades API
                exit_price_actual = None
                try:
                    trades = binance_client.user_trades(
                        symbol=symbol,
                        start_time=entry_ts,
                        limit=100,
                    )
                    # Filter to closing (reduceOnly) trades; compute VWAP
                    close_trades = [t for t in trades if t.get("buyer") != t.get("maker")]
                    # More reliable: use the last few trades that are "reduce-only" by checking positionSide or realizedPnl
                    close_trades_pnl = [t for t in trades if float(t.get("realizedPnl", 0)) != 0]
                    if close_trades_pnl:
                        total_qty = sum(float(t["qty"]) for t in close_trades_pnl)
                        if total_qty > 0:
                            exit_price_actual = round(
                                sum(float(t["price"]) * float(t["qty"]) for t in close_trades_pnl) / total_qty,
                                2,
                            )
                except Exception as te:
                    logger.warning("userTrades lookup for exit price failed: %s", te)

                # 5) Compute balance-based PnL %
                acct = binance_client.account()
                total_balance = float(acct.get("totalWalletBalance", 0) or 0)
                balance_before_exit = total_balance - realized_pnl
                pnl_pct_of_balance = (
                    (realized_pnl / balance_before_exit * 100.0)
                    if balance_before_exit and balance_before_exit != 0
                    else 0.0
                )
                _daily_realized_pnl += realized_pnl

                # 6) Build exit journal payload
                exit_payload: dict[str, Any] = {
                    "type": "exit",
                    "symbol": symbol,
                    "realized_pnl": round(realized_pnl, 2),
                    "balance_before_exit": round(balance_before_exit, 2),
                    "pnl_pct_of_balance": round(pnl_pct_of_balance, 2),
                }
                if last_entry:
                    exit_payload["side"] = last_entry.get("side")
                    exit_payload["entry_price"] = last_entry.get("entry_price")
                    exit_payload["qty"] = last_entry.get("qty")
                    exit_payload["client_order_id"] = last_entry.get("client_order_id")
                # Use actual exit price from trades, else approximate
                if exit_price_actual is not None:
                    exit_payload["exit_price"] = exit_price_actual
                elif last_entry:
                    ep, q = last_entry.get("entry_price"), last_entry.get("qty")
                    if ep is not None and q and float(q) != 0:
                        side_val = last_entry.get("side", "BUY")
                        if side_val == "BUY":
                            exit_payload["exit_price"] = round(float(ep) + realized_pnl / float(q), 2)
                        else:
                            exit_payload["exit_price"] = round(float(ep) - realized_pnl / float(q), 2)
                journal_append(exit_payload)
            except Exception as je:
                logger.warning("Exit journal append failed: %s", je)
        if has_position_now:
            _symbols_with_position_last_cycle.add(symbol)
        else:
            _symbols_with_position_last_cycle.discard(symbol)

        entry_tf = cfg.entry_tf
        trend_tf = cfg.trend_tf if cfg.trend_tf != entry_tf else entry_tf
        intervals = list(dict.fromkeys([entry_tf, trend_tf]))
        candles_by_tf: dict[str, list[MarketDataCandle]] = {}
        for interval in intervals:
            raw = binance_client.klines(symbol=symbol, interval=interval, limit=100)
            candles_by_tf[interval] = [
                MarketDataCandle(
                    time=k[0] // 1000,
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                )
                for k in raw
            ]
        if not candles_by_tf.get(entry_tf):
            return
        current_price = candles_by_tf[entry_tf][-1].close
        volumes = [c.volume for c in candles_by_tf[entry_tf][-21:-1]]
        vol_sum = sum(volumes)
        volume_ratio = (
            (volumes[-1] / (vol_sum / len(volumes))) if len(volumes) >= 10 and vol_sum and vol_sum > 0 else 1.0
        )
        funding_rate = 0.0
        try:
            fr = binance_client.funding_rate(symbol=symbol, limit=1)
            if fr:
                funding_rate = float(fr[0].get("fundingRate", 0))
        except Exception:
            pass
        data = MarketData(
            symbol=symbol,
            candles=candles_by_tf,
            funding_rate=funding_rate,
            volume_ratio=volume_ratio,
            current_price=current_price,
        )
        # Resolve effective strategy mode (auto → use 1h regime)
        raw_mode = getattr(cfg, "strategy_mode", "trend")
        if raw_mode == "auto":
            try:
                regime_data = get_market_regime(symbol)
                h1_regime = regime_data.get("1h", {}).get("regime", "unknown")
                effective_mode = "range" if h1_regime == "ranging" else "trend"
                _append_activity("system", symbol, f"Auto mode: 1h regime={h1_regime} → {effective_mode}")
            except Exception as re_err:
                logger.warning("Auto regime lookup failed: %s, defaulting to trend", re_err)
                effective_mode = "trend"
        else:
            effective_mode = raw_mode
        strategy = RangeRSIStrategy() if effective_mode == "range" else ConfluenceATRStrategy()
        signal, skip_reason = strategy.get_signal(data, cfg)
        if signal is None or signal.side == "flat":
            _append_activity("signal", symbol, f"No entry: {skip_reason}")
            return
        mode_label = "RSI/range" if effective_mode == "range" else "RSI/MACD/trend"
        _append_activity(
            "signal",
            symbol,
            f"{signal.side.upper()} {mode_label} | {signal.reason}",
            {"entry": signal.entry_price, "sl": signal.stop_loss, "tp": signal.take_profit},
        )
        leverage = min(cfg.max_leverage, 125)
        notional = cfg.max_usdt * leverage
        qty = notional / current_price if current_price else 0
        if qty <= 0:
            _append_activity("error", symbol, "Invalid quantity")
            return
        filters = ExchangeInfoCache.get_symbol_filters(symbol)
        step = filters.get("step_size", 0.001)
        if step > 0:
            import math
            prec = max(0, -int(round(math.log10(step))))
            qty = round(qty - (qty % step), prec)
        min_notional = filters.get("min_notional", 5.0)
        if (qty * current_price) < min_notional:
            _append_activity("skip", symbol, f"Below min notional {min_notional}")
            return
        sl_price = ExchangeInfoCache.round_price(symbol, signal.stop_loss)
        tp_price = ExchangeInfoCache.round_price(symbol, signal.take_profit)
        side = "BUY" if signal.side == "long" else "SELL"

        # Current position (live) for same-direction skip and opposite-direction flip (lp from start of cycle)
        current_side: str | None = None
        current_notional = 0.0
        if lp:
            current_side = lp["side"]
            current_notional = lp["notional"]

        do_entry = False
        if current_side is None:
            cooldown = cfg.reentry_cooldown_minutes * 60.0
            if cooldown > 0 and symbol in _symbol_exit_ts:
                if (time.time() - _symbol_exit_ts[symbol]) < cooldown:
                    _append_activity("skip", symbol, "Reentry cooldown")
                    return
            do_entry = True
        elif current_side == side:
            _append_activity("skip", symbol, "Already in position (same direction)")
            return
        else:
            # Opposite direction: allow flip only if economically justified
            if not getattr(cfg, "allow_position_flip", True):
                _append_activity("skip", symbol, "Opposite signal but flip disabled")
                return
            fee_bps = getattr(cfg, "flip_fee_bps", 8.0)
            slip_bps = getattr(cfg, "flip_slippage_bps", 5.0)
            min_ratio = getattr(cfg, "flip_min_edge_ratio", 1.5)
            flip_cost = current_notional * (fee_bps + slip_bps) * 2 / 10000.0
            new_upside = (tp_price - current_price) * qty if signal.side == "long" else (current_price - tp_price) * qty
            if new_upside < flip_cost * min_ratio:
                _append_activity(
                    "skip",
                    symbol,
                    f"Flip not justified: cost={flip_cost:.2f} USDT, upside={new_upside:.2f} (need >={flip_cost * min_ratio:.2f})",
                )
                return
            # Live flip: cancel all open orders and algo orders (old SL/TP) for symbol, then close position, then new entry
            pos = _get_live_position(symbol)
            if not pos:
                return
            try:
                binance_client.cancel_all_open_orders(symbol=symbol)
            except Exception as e:
                _append_activity("error", symbol, f"Flip cancel open orders failed: {e}")
                return
            try:
                binance_client.cancel_all_algo_orders(symbol=symbol)
            except Exception as e:
                _append_activity("error", symbol, f"Flip cancel algo orders failed: {e}")
                return
            close_side = "SELL" if pos["side"] == "BUY" else "BUY"
            close_qty = ExchangeInfoCache.round_quantity(symbol, pos["qty"])
            try:
                binance_client.new_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="MARKET",
                    quantity=close_qty,
                    reduce_only=True,
                )
            except Exception as e:
                _append_activity("error", symbol, f"Flip close failed: {e}")
                return
            _append_activity("exit", symbol, f"Flip close {pos['side']} qty={close_qty} @ market")
            do_entry = True

        if not do_entry:
            return

        # Live: size position by actual available balance (use up to 98%, capped by max_usdt)
        try:
            bal_list = binance_client.balance()
            usdt = next((b for b in bal_list if str(b.get("asset", "")).upper() == "USDT"), None)
            available = float(usdt.get("availableBalance", 0) or 0) if usdt else 0.0
        except Exception:
            available = 0.0
        effective_margin = min(available * 0.98, cfg.max_usdt)
        min_margin = 5.0
        if effective_margin < min_margin:
            _append_activity(
                "skip",
                symbol,
                f"Insufficient balance: available {available:.2f} USDT (need at least {min_margin} USDT to open a position).",
            )
            return
        notional = effective_margin * leverage
        qty = notional / current_price if current_price else 0
        if qty <= 0:
            _append_activity("error", symbol, "Invalid quantity after balance-based sizing")
            return
        filters = ExchangeInfoCache.get_symbol_filters(symbol)
        step = filters.get("step_size", 0.001)
        if step > 0:
            import math
            prec = max(0, -int(round(math.log10(step))))
            qty = round(qty - (qty % step), prec)
        min_notional = filters.get("min_notional", 5.0)
        if (qty * current_price) < min_notional:
            _append_activity("skip", symbol, f"Balance too low for min notional {min_notional} USDT (available {available:.2f} USDT)")
            return
        try:
            binance_client.set_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            logger.warning("Set leverage failed: %s", e)
        try:
            binance_client.cancel_all_algo_orders(symbol=symbol)
        except Exception as e:
            logger.warning("Cancel algo orders before entry: %s", e)
        client_order_id = f"ap_{uuid.uuid4().hex[:16]}"
        try:
            binance_client.new_order(
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=qty,
                new_client_order_id=client_order_id,
            )
        except Exception as e:
            err_msg = str(e)
            if "-2019" in err_msg or "Margin is insufficient" in err_msg or "insufficient" in err_msg.lower():
                _append_activity(
                    "error",
                    symbol,
                    "Entry order failed: Margin is insufficient. Check Futures wallet balance and reduce max_usdt/leverage in Rich Man settings.",
                )
            else:
                _append_activity("error", symbol, f"Entry order failed: {err_msg}")
            return
        _append_activity("entry", symbol, f"{side} qty={qty} @ {current_price:.2f} SL={sl_price:.2f} TP={tp_price:.2f}")
        try:
            journal_append({
                "type": "entry",
                "symbol": symbol,
                "side": side,
                "entry_price": current_price,
                "qty": qty,
                "sl": sl_price,
                "tp": tp_price,
                "client_order_id": client_order_id,
                "entry_ts": int(time.time() * 1000),  # ms timestamp for exit PnL lookup
            })
        except Exception as je:
            logger.warning("Journal append failed: %s", je)
        stop_side = "SELL" if signal.side == "long" else "BUY"
        try:
            binance_client.new_algo_order(
                symbol=symbol,
                side=stop_side,
                order_type="STOP_MARKET",
                trigger_price=sl_price,
                quantity=qty,
                reduce_only=True,
                client_algo_id=f"{client_order_id}_sl",
            )
        except Exception as e:
            _append_activity("error", symbol, f"SL order failed: {e}")
        try:
            binance_client.new_algo_order(
                symbol=symbol,
                side=stop_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=tp_price,
                quantity=qty,
                reduce_only=True,
                client_algo_id=f"{client_order_id}_tp",
            )
        except Exception as e:
            _append_activity("error", symbol, f"TP order failed: {e}")
    except Exception as e:
        logger.exception("Autopilot cycle error: %s", e)
        _append_activity("error", symbol, str(e))


def set_symbol_exit_ts(symbol: str) -> None:
    global _symbol_exit_ts
    _symbol_exit_ts[symbol] = time.time()


def _autopilot_loop() -> None:
    import threading
    global _stop_loop
    _stop_loop = False
    while not _stop_loop:
        try:
            run_one_cycle()
        except Exception as e:
            logger.exception("Autopilot loop: %s", e)
        for _ in range(_cycle_interval_sec):
            if _stop_loop:
                break
            time.sleep(1)


def _start_loop() -> None:
    import threading
    global _loop_thread, _stop_loop
    _stop_loop = False
    if _loop_thread is not None and _loop_thread.is_alive():
        return
    _loop_thread = threading.Thread(target=_autopilot_loop, daemon=True)
    _loop_thread.start()


def _stop_loop_thread() -> None:
    global _stop_loop
    _stop_loop = True

