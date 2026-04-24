"""Engine service: runs BFAT engine + WebSocket streams in background."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from app.config.settings import Settings
from app.core.database import DatabaseFactory
from app.core.engine import BFATEngine
from app.core.execution import BinanceExecutionClient, _generate_client_order_id
from app.core.market.binance_market_stream import BinanceMarketStream
from app.core.risk import KillSwitch, RiskManager
from app.core.ws.binance_user_stream import BinanceUserStream
from app.domain.enums import Side, StopPhase
from app.domain.position import Position
from app.domain.state_machine import StateMachine
from app.domain.strategy_engine import StrategyEngine
from app.core.notification import TelegramNotifier
from app.persistence import create_persistence
from app.services.binance_account import BinanceAccountClient

logger = logging.getLogger(__name__)


BINANCE_REST_MAINNET = "https://fapi.binance.com"
BINANCE_REST_TESTNET = "https://testnet.binancefuture.com"


def _extract_equity_from_account(acct: dict) -> float:
    """Extract equity from Binance account response. Handles camelCase, snake_case, string values."""
    for k in (
        "totalMarginBalance",
        "totalWalletBalance",
        "total_margin_balance",
        "total_wallet_balance",
    ):
        val = acct.get(k)
        if val is not None and val != "":
            try:
                f = float(val)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
    for a in acct.get("assets") or []:
        if str(a.get("asset", "")).upper() == "USDT":
            for k in ("marginBalance", "walletBalance", "crossWalletBalance", "margin_balance", "wallet_balance"):
                v = a.get(k)
                if v is not None and v != "":
                    try:
                        f = float(v)
                        if f > 0:
                            return f
                    except (TypeError, ValueError):
                        pass
    return 0.0


class EngineService:
    """Wraps engine + streams. Runs in background. Exposes status."""

    def __init__(
        self,
        settings: Settings,
        db_factory: DatabaseFactory,
        binance_account: BinanceAccountClient,
    ) -> None:
        self._settings = settings
        self._db = db_factory
        self._binance_account = binance_account
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._engine: Optional[BFATEngine] = None
        self._market_stream: Optional[BinanceMarketStream] = None
        self._user_stream: Optional[BinanceUserStream] = None
        self._critical_error: Optional[str] = None
        self._equity_value: float = 0.0
        self._equity_ts: float = 0.0
        self._live_pos_cache: dict[str, Any] | None = None
        self._live_pos_ts: float = 0.0
        rest_base = (
            BINANCE_REST_TESTNET
            if self._settings.binance_testnet
            else BINANCE_REST_MAINNET
        )
        self._readonly_execution = BinanceExecutionClient(
            api_key=self._settings.binance_api_key,
            api_secret=self._settings.binance_api_secret,
            base_url=rest_base,
            testnet=self._settings.binance_testnet,
        )

    def _build_engine(self) -> BFATEngine:
        """Construct engine with all dependencies."""
        trade_repo, equity_repo, system_log_repo = create_persistence(self._db)
        rest_base = (
            BINANCE_REST_TESTNET
            if self._settings.binance_testnet
            else BINANCE_REST_MAINNET
        )
        execution = BinanceExecutionClient(
            api_key=self._settings.binance_api_key,
            api_secret=self._settings.binance_api_secret,
            base_url=rest_base,
            testnet=self._settings.binance_testnet,
        )
        kill_switch = KillSwitch()
        risk_manager = RiskManager()
        state_machine = StateMachine()
        strategy_engine = StrategyEngine(symbol=self._settings.bfat_symbol)
        notifier = TelegramNotifier(
            bot_token=self._settings.telegram_bot_token,
            chat_id=self._settings.telegram_chat_id,
            enabled=self._settings.telegram_enabled,
        )
        return BFATEngine(
            strategy_engine=strategy_engine,
            risk_manager=risk_manager,
            kill_switch=kill_switch,
            execution_client=execution,
            state_machine=state_machine,
            trade_repository=trade_repo,
            equity_repository=equity_repo,
            system_log_repository=system_log_repo,
            symbol=self._settings.bfat_symbol,
            notifier=notifier,
            scaleout_enabled=self._settings.bfat_scaleout_enabled,
            scaleout_r_multiple=self._settings.bfat_scaleout_r_multiple,
            scaleout_fraction=self._settings.bfat_scaleout_fraction,
        )

    _EQUITY_TTL = 15.0  # seconds — fetch from Binance at most once per TTL

    def _equity_provider(self) -> float:
        """Return equity with short TTL. Fetches from Binance if stale; returns last known value on failure."""
        now = time.monotonic()
        if now - self._equity_ts < self._EQUITY_TTL:
            return self._equity_value
        if not self._binance_account.is_configured():
            return self._equity_value
        try:
            acct = self._binance_account.account()
            total = _extract_equity_from_account(acct)
            self._equity_value = total
            self._equity_ts = now
            return total
        except Exception as e:
            logger.warning("Equity fetch failed: %s", e)
            return self._equity_value

    _LIVE_POS_TTL = 30.0  # seconds — rate-limit Binance position queries

    def _fetch_binance_position(self) -> dict[str, Any] | None:
        """Query Binance for a live position. Cached for _LIVE_POS_TTL seconds.

        Uses positionRisk (get_position) for accurate entry_price; also queries
        Open Orders for STOP_MARKET to populate stop_price.
        """
        now = time.monotonic()
        if now - self._live_pos_ts < self._LIVE_POS_TTL:
            return self._live_pos_cache
        if not self._binance_account.is_configured():
            return None
        try:
            symbol = self._settings.bfat_symbol
            exec_client = (
                self._engine._execution if self._engine else self._readonly_execution
            )
            pos_data = exec_client.get_position(symbol)
            result = None
            if pos_data and isinstance(pos_data, dict):
                amt = float(pos_data.get("positionAmt") or pos_data.get("position_amt") or 0)
                if amt != 0:
                    ep = pos_data.get("entryPrice") or pos_data.get("entry_price") or 0
                    entry_price = float(ep) if ep else 0.0
                    up = pos_data.get("unRealizedProfit") or pos_data.get("unrealizedProfit") or 0
                    unrealized = float(up) if up else 0.0
                    update_ts = pos_data.get("updateTime") or pos_data.get("update_time")
                    entry_time = ""
                    if update_ts:
                        try:
                            entry_time = datetime.fromtimestamp(
                                int(update_ts) / 1000, tz=timezone.utc
                            ).isoformat(timespec="seconds") + "Z"
                        except (ValueError, TypeError, OSError):
                            pass
                    result = {
                        "symbol": symbol,
                        "side": "long" if amt > 0 else "short",
                        "size": abs(amt),
                        "entry_price": entry_price,
                        "stop_price": 0,
                        "initial_stop_price": 0,
                        "stop_phase": "unknown",
                        "entry_time": entry_time,
                        "correlation_id": "binance_live",
                        "unrealized_pnl": unrealized,
                        "source": "binance",
                        "take_profit": None,
                    }
            if result is not None:
                exec_client = (
                    self._engine._execution if self._engine else self._readonly_execution
                )
                try:
                    for ao in exec_client.get_open_algo_orders(symbol):
                        st = ao.get("algoStatus") or ao.get("status")
                        if st != "NEW":
                            continue
                        ot = ao.get("orderType") or ao.get("type")
                        tr = ao.get("triggerPrice")
                        px = float(tr) if tr not in (None, "") else 0.0
                        if ot == "STOP_MARKET" and px > 0:
                            result["stop_price"] = px
                            result["initial_stop_price"] = px
                            result["stop_phase"] = "active"
                        elif ot == "TAKE_PROFIT_MARKET" and px > 0:
                            result["take_profit"] = px
                    open_orders = exec_client.get_open_orders(symbol)
                    for order in open_orders:
                        if order.get("type") in ("STOP_MARKET", "STOP") and order.get("status") == "NEW":
                            sp = float(order.get("stopPrice", 0))
                            if sp > 0 and (not result.get("stop_price") or result["stop_price"] <= 0):
                                result["stop_price"] = sp
                                result["initial_stop_price"] = sp
                                result["stop_phase"] = "active"
                            break
                except Exception as e:
                    logger.debug("Open orders fetch for SL failed: %s", e)
            self._live_pos_cache = result
            self._live_pos_ts = now
            return result
        except Exception as e:
            logger.debug("Binance live position fetch failed: %s", e)
            return self._live_pos_cache

    _INSIGHT_STALE_THRESHOLD_S = 120.0  # 2 min without insight update → stale

    def _build_stream_diagnostics(self) -> dict[str, Any]:
        """Collect diagnostics from market and user streams."""
        now = time.time()
        market = self._market_stream.get_diagnostics() if self._market_stream else None
        user = self._user_stream.get_diagnostics() if self._user_stream else None

        insight_ts: float = 0.0
        if self._engine is not None:
            se = getattr(self._engine, "_strategy_engine", None)
            if se is not None:
                insight_ts = getattr(se, "_last_insight_update_ts", 0.0)
        insight_age = round(now - insight_ts, 1) if insight_ts > 0 else None
        insight_stale = insight_age is not None and insight_age > self._INSIGHT_STALE_THRESHOLD_S

        return {
            "market_stream": market,
            "user_stream": user,
            "last_insight_update_ts": insight_ts if insight_ts > 0 else None,
            "insight_age_seconds": insight_age,
            "insight_stale": insight_stale,
        }

    def _get_status(self) -> dict[str, Any]:
        """Build status dict for API/WebSocket."""
        equity = self._equity_provider()
        diagnostics = self._build_stream_diagnostics()
        if self._engine is None:
            live_pos = self._fetch_binance_position()
            live_stop = live_pos.get("stop_price", 0) if live_pos else 0
            live_tp = live_pos.get("take_profit") if live_pos else None
            live_tp_f = float(live_tp) if live_tp not in (None, "") else None
            stopped_unrealized = None
            if live_pos is not None:
                stopped_unrealized = live_pos.get("unrealized_pnl")
            return {
                "engine_state": "stopped",
                "position": live_pos,
                "last_signal": None,
                "current_stop_price": live_stop if live_stop > 0 else None,
                "take_profit": live_tp_f if live_tp_f and live_tp_f > 0 else None,
                "tp_protection_mode": "none",
                "tp_verified": None,
                "tp_status": "none",
                "tp_error": None,
                "sl_protection_mode": "none",
                "sl_verified": None,
                "r_multiple": None,
                "r_validation_status": None,
                "system_health": "HEALTHY",
                "equity": equity,
                "unrealized_pnl": stopped_unrealized,
                "total_realized_pnl": None,
                "kill_switch_triggered": False,
                "post_close_cooldown": 0,
                "error": self._critical_error,
                "diagnostics": diagnostics,
            }
        sm = self._engine._state_machine
        pos = sm.position
        pos_dict = None
        if pos is not None:
            pos_dict = {
                "symbol": pos.symbol,
                "side": pos.side.value,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "stop_price": pos.stop_price,
                "initial_stop_price": pos.initial_stop_price,
                "stop_phase": pos.stop_phase.value,
                "entry_time": pos.entry_time,
                "correlation_id": pos.correlation_id,
                "take_profit": pos.take_profit,
            }
        if pos_dict is None:
            live_pos = self._fetch_binance_position()
            if live_pos is not None:
                pos_dict = live_pos
        last_signal = None
        r_multiple = None
        r_validation_status = None
        trade_repo = self._engine._trade_repo
        trades = trade_repo.query(symbol=self._settings.bfat_symbol, limit=1)
        if trades:
            t = trades[0]
            last_signal = {
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "signal_time": t.get("entry_time", ""),
                "signal_candle_ts": t.get("signal_candle_ts", "") or "",
            }
            r_val = t.get("pnl_r")
            r_multiple = float(r_val) if r_val is not None else None
            r_validation_status = t.get("r_validation_status") or None
        kill = self._engine._kill_switch
        system_health = "HEALTHY"
        if self._critical_error:
            system_health = "DEGRADED"
        elif r_validation_status in ("CRITICAL", "CRITICAL_OUTLIER"):
            system_health = "DEGRADED"
        take_profit = (
            pos.take_profit
            if pos is not None
            else getattr(self._engine, "_current_take_profit", None)
        )
        tp_algo_id = getattr(self._engine, "_current_tp_algo_id", None)
        tp_status_raw = getattr(self._engine, "_tp_status", "none")
        tp_last_error = getattr(self._engine, "_tp_last_error", None)
        if take_profit is not None and float(take_profit) > 0:
            tp_protection_mode = "exchange" if tp_algo_id else "fallback"
        else:
            tp_protection_mode = "none"
        if tp_status_raw == "failed":
            tp_protection_mode = "failed"
        elif tp_status_raw == "repriced" and tp_algo_id:
            tp_protection_mode = "repriced"
        tp_verified = tp_algo_id is not None if tp_protection_mode in ("exchange", "repriced") else None
        sl_order_id = getattr(self._engine, "_current_stop_order_id", None)
        sl_verified_flag = getattr(self._engine, "_sl_verified", False)
        if pos is not None and sl_order_id:
            sl_protection_mode = "exchange" if sl_verified_flag else "recovering"
        elif pos is not None:
            sl_protection_mode = "recovering"
        else:
            sl_protection_mode = "none"
        sl_verified = sl_verified_flag if sl_protection_mode == "exchange" else None
        unrealized_pnl: float | None = None
        if pos is not None:
            try:
                exec_client = self._engine._execution
                live_data = exec_client.get_position(self._settings.bfat_symbol)
                if live_data:
                    up = live_data.get("unRealizedProfit") or live_data.get("unrealizedProfit") or 0
                    unrealized_pnl = float(up) if up else 0.0
            except Exception:
                pass
        elif pos_dict is not None:
            unrealized_pnl = pos_dict.get("unrealized_pnl")
        total_realized_pnl: float | None = None
        try:
            summary = self.get_trade_summary(symbol=self._settings.bfat_symbol)
            total_realized_pnl = summary.get("total_net_pnl", 0.0)
        except Exception:
            pass
        cooldown_remaining = getattr(self._engine, "_post_close_cooldown_remaining", 0)

        if diagnostics.get("insight_stale"):
            system_health = "DEGRADED"

        market_diag = diagnostics.get("market_stream")
        if market_diag and not market_diag.get("connected"):
            system_health = "DEGRADED"

        return {
            "engine_state": sm.state.value,
            "position": pos_dict,
            "last_signal": last_signal,
            "current_stop_price": (
                pos.stop_price if pos
                else (pos_dict.get("stop_price", 0) or None) if pos_dict and pos_dict.get("stop_price", 0) > 0
                else None
            ),
            "take_profit": take_profit,
            "tp_protection_mode": tp_protection_mode,
            "tp_verified": tp_verified,
            "tp_status": tp_status_raw,
            "tp_error": tp_last_error,
            "sl_protection_mode": sl_protection_mode,
            "sl_verified": sl_verified,
            "r_multiple": r_multiple,
            "r_validation_status": r_validation_status,
            "system_health": system_health,
            "equity": equity,
            "unrealized_pnl": unrealized_pnl,
            "total_realized_pnl": total_realized_pnl,
            "kill_switch_triggered": kill.is_triggered(),
            "post_close_cooldown": cooldown_remaining,
            "error": self._critical_error,
            "diagnostics": diagnostics,
        }

    def _sync_binance_position(self) -> None:
        """On engine start, check Binance for an existing open position and restore into StateMachine.

        FAIL-CLOSED POLICY: if SL cannot be verified on exchange after all
        recovery attempts, the position is NOT restored. The engine starts
        FLAT, and the unprotected position remains on Binance for manual
        intervention (logged as CRITICAL).
        """
        if self._engine is None:
            return
        symbol = self._settings.bfat_symbol
        exec_client = self._engine._execution
        sm = self._engine._state_machine

        # ── Hedge-mode guard ──
        try:
            pos_risk = exec_client._request("GET", "/fapi/v2/positionRisk", {})
            if isinstance(pos_risk, list):
                sides_for_sym = [
                    p.get("positionSide")
                    for p in pos_risk
                    if isinstance(p, dict) and p.get("symbol") == symbol
                ]
                if "LONG" in sides_for_sym or "SHORT" in sides_for_sym:
                    both_present = "LONG" in sides_for_sym and "SHORT" in sides_for_sym
                    non_zero = sum(
                        1 for p in pos_risk
                        if isinstance(p, dict)
                        and p.get("symbol") == symbol
                        and float(p.get("positionAmt") or 0) != 0
                    )
                    if both_present and non_zero > 0:
                        logger.error(
                            "Hedge Mode detected for %s. BFAT requires One-Way mode. "
                            "Position sync aborted.",
                            symbol,
                        )
                        return
        except Exception as e:
            logger.debug("Hedge-mode check failed (non-fatal): %s", e)

        try:
            pos_data = exec_client.get_position(symbol)
            if not pos_data:
                return
            pos_amt = float(pos_data.get("positionAmt") or pos_data.get("position_amt") or 0)
            if pos_amt == 0:
                return
            ep = pos_data.get("entryPrice") or pos_data.get("entry_price") or 0
            entry_price = float(ep) if ep else 0.0
            if entry_price <= 0:
                return
            side = Side.LONG if pos_amt > 0 else Side.SHORT
            size = abs(pos_amt)

            stop_order_id: str | None = None
            tp_order_id: str | None = None
            stop_price = entry_price
            take_profit_price: float | None = None
            try:
                for ao in exec_client.get_open_algo_orders(symbol):
                    st = ao.get("algoStatus") or ao.get("status")
                    if st != "NEW":
                        continue
                    ot = ao.get("orderType") or ao.get("type")
                    aid = ao.get("algoId")
                    tr = ao.get("triggerPrice")
                    px = float(tr) if tr not in (None, "") else 0.0
                    if ot == "STOP_MARKET" and stop_order_id is None:
                        if aid is not None:
                            stop_order_id = str(aid)
                        if px > 0:
                            stop_price = px
                    elif ot == "TAKE_PROFIT_MARKET" and tp_order_id is None:
                        if aid is not None:
                            tp_order_id = str(aid)
                        if px > 0:
                            take_profit_price = px
                if stop_order_id is None:
                    open_orders = exec_client.get_open_orders(symbol)
                    for order in open_orders:
                        if order.get("type") in ("STOP_MARKET", "STOP") and order.get("status") == "NEW":
                            stop_order_id = str(order["orderId"])
                            sp = float(order.get("stopPrice", 0))
                            if sp > 0:
                                stop_price = sp
                            break
            except Exception as e:
                logger.warning("Failed to fetch open orders during sync: %s", e)

            if stop_order_id is not None and stop_price > 0:
                valid_stop = (
                    (side == Side.LONG and stop_price < entry_price)
                    or (side == Side.SHORT and stop_price > entry_price)
                )
                if not valid_stop:
                    logger.warning(
                        "Restored stop %.4f is on wrong side of entry %.4f for %s %s; "
                        "cancelling and re-registering",
                        stop_price, entry_price, side.value, symbol,
                    )
                    try:
                        exec_client.cancel_order(symbol, stop_order_id)
                    except Exception:
                        pass
                    stop_order_id = None

            # ── SL recovery: place + verify with backoff ──
            sl_verified = False
            if stop_order_id is None:
                _SAFETY_MARGINS = [0.015, 0.030, 0.050]
                for margin in _SAFETY_MARGINS:
                    if side == Side.LONG:
                        stop_price = exec_client.format_price(
                            symbol, entry_price * (1 - margin),
                        )
                    else:
                        stop_price = exec_client.format_price(
                            symbol, entry_price * (1 + margin), ceil=True,
                        )
                    try:
                        stop_resp = exec_client.place_stop_market_order(
                            symbol, side, size, stop_price,
                            _generate_client_order_id("bfat_restore_stop"),
                        )
                        new_id = str(stop_resp.get("orderId", ""))
                        if new_id and exec_client.verify_algo_order_with_backoff(symbol, new_id):
                            stop_order_id = new_id
                            sl_verified = True
                            logger.info(
                                "Placed and verified safety stop: %s @ %.4f (margin=%.1f%%)",
                                stop_order_id, stop_price, margin * 100,
                            )
                            break
                        elif new_id:
                            logger.warning("Safety SL placed (%s) but not verified at margin %.1f%%", new_id, margin * 100)
                    except Exception as e:
                        logger.warning("Safety SL at %.1f%% failed: %s", margin * 100, e)
                        continue
            else:
                try:
                    sl_verified = exec_client.verify_algo_order_with_backoff(symbol, stop_order_id)
                except Exception as e:
                    logger.warning("SL verification failed for existing order %s: %s", stop_order_id, e)

            if not sl_verified and stop_order_id:
                logger.warning("Existing SL %s not verified; attempting re-registration", stop_order_id)
                try:
                    exec_client.cancel_order(symbol, stop_order_id)
                except Exception:
                    pass
                re_stop = exec_client.format_price(
                    symbol,
                    entry_price * (1 - 0.015) if side == Side.LONG else entry_price * (1 + 0.015),
                    ceil=(side == Side.SHORT),
                )
                re_id = _generate_client_order_id("bfat_restore_stop2")
                try:
                    re_resp = exec_client.place_stop_market_order(symbol, side, size, re_stop, re_id)
                    new_id = str(re_resp.get("orderId", ""))
                    if new_id and exec_client.verify_algo_order_with_backoff(symbol, new_id):
                        stop_order_id = new_id
                        stop_price = re_stop
                        sl_verified = True
                        logger.info("Re-registered and verified SL: %s @ %.4f", new_id, re_stop)
                    else:
                        logger.error("Re-registered SL %s still not verified", new_id)
                except Exception as e2:
                    logger.error("SL re-registration failed during sync: %s", e2)

            # ── FAIL-CLOSED: refuse to restore position without verified SL ──
            if not sl_verified:
                logger.critical(
                    "FAIL-CLOSED: Cannot verify SL for %s %s %.6f @ %.4f. "
                    "Position will NOT be restored into engine. "
                    "Manual intervention required on Binance.",
                    side.value, symbol, size, entry_price,
                )
                _, _, sys_log = create_persistence(self._db)
                sys_log.insert(
                    level="CRITICAL",
                    event="sync_fail_closed_no_sl",
                    message=(
                        f"Engine start: position {side.value} {symbol} {size} @ {entry_price} "
                        f"found on Binance but SL could not be verified. "
                        f"Position NOT restored. Manual intervention required."
                    ),
                )
                self._critical_error = (
                    f"Position found on Binance but SL could not be secured. "
                    f"Engine started FLAT. Check Binance manually."
                )
                return

            update_ts = pos_data.get("updateTime") or pos_data.get("update_time")
            if update_ts:
                try:
                    entry_time = datetime.fromtimestamp(int(update_ts) / 1000, tz=timezone.utc).isoformat(timespec="seconds") + "Z"
                except (ValueError, TypeError, OSError):
                    entry_time = ""
            else:
                entry_time = ""

            position = Position(
                symbol=symbol,
                side=side,
                size=size,
                entry_price=entry_price,
                stop_price=stop_price,
                initial_stop_price=stop_price,
                stop_phase=StopPhase.INITIAL,
                entry_time=entry_time,
                correlation_id="restored_from_binance",
                take_profit=take_profit_price,
            )
            sm.restore_position(position)
            self._engine._current_stop_order_id = stop_order_id
            self._engine._sl_verified = True
            self._engine._current_tp_algo_id = tp_order_id
            self._engine._current_take_profit = take_profit_price

            if tp_order_id:
                try:
                    if not exec_client.verify_algo_order_active(symbol, tp_order_id):
                        logger.warning(
                            "TP order %s not confirmed as active on exchange for %s",
                            tp_order_id, symbol,
                        )
                        self._engine._current_tp_algo_id = None
                except Exception as e:
                    logger.debug("TP verification check failed: %s", e)

            logger.info(
                "Restored Binance position: %s %s %.6f @ %.4f, stop=%.4f (order=%s, verified=%s), tp=%s (order=%s)",
                side.value, symbol, size, entry_price, stop_price, stop_order_id,
                sl_verified, take_profit_price, tp_order_id,
            )

            # Pre-evaluate regime and recover missing TP
            if sm.position is not None:
                try:
                    kline_url = f"{exec_client._base_url}/fapi/v1/klines"
                    kline_params = {"symbol": symbol.upper(), "interval": "15m", "limit": 100}
                    kline_resp = requests.get(kline_url, params=kline_params, timeout=10)
                    if kline_resp.status_code == 200:
                        raw = kline_resp.json()
                        candles = []
                        for bar in raw:
                            if len(bar) < 6:
                                continue
                            candles.append({
                                "open": float(bar[1]),
                                "high": float(bar[2]),
                                "low": float(bar[3]),
                                "close": float(bar[4]),
                                "volume": float(bar[5]),
                                "timestamp": str(bar[6]),
                            })
                        if candles:
                            se = self._engine._strategy_engine
                            pre_regime = se.regime_classifier.evaluate(candles)
                            se._active_regime = pre_regime
                            rc_details = se.regime_classifier.get_last_details()
                            se._last_trend_direction = rc_details.get("trend_direction", "neutral")
                            logger.info(
                                "Pre-evaluated regime for restored position: %s (trend=%s)",
                                pre_regime, se._last_trend_direction,
                            )

                            if take_profit_price is None and self._engine._current_tp_algo_id is None:
                                self._recover_tp_from_atr(candles, position, exec_client)
                except Exception as e:
                    logger.warning("Regime pre-evaluation failed (non-fatal): %s", e)

        except Exception as e:
            logger.warning("Binance position sync failed: %s", e)

    _TP_ATR_MULTIPLIER = 2.8
    _SL_ATR_MULTIPLIER = 1.6

    def _recover_tp_from_atr(
        self, candles: list[dict], position: Position, exec_client: BinanceExecutionClient,
    ) -> None:
        """Calculate and place a missing TP order using ATR, with price-aware repricing."""
        if len(candles) < 15:
            return
        tr_list = [0.0]
        for i in range(1, len(candles)):
            h, lo, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
            tr_list.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr_val = sum(tr_list[-14:]) / 14
        if atr_val <= 0:
            return

        if position.side == Side.LONG:
            tp_price = position.entry_price + self._TP_ATR_MULTIPLIER * atr_val
        else:
            tp_price = position.entry_price - self._TP_ATR_MULTIPLIER * atr_val
        tp_price = exec_client.format_price(
            position.symbol, tp_price, ceil=(position.side == Side.LONG),
        )
        if tp_price <= 0:
            return

        current_price = exec_client.get_ticker_price(position.symbol)
        if current_price <= 0:
            current_price = candles[-1]["close"]

        from app.core.engine.engine import (
            _reprice_tp_outside_market,
            _validate_take_profit_price,
        )

        repriced = False
        if not _validate_take_profit_price(position.side, tp_price, position.entry_price, current_price):
            filters = exec_client._get_filters(position.symbol)
            tick_size = filters.get("price_step", 0.1)
            tp_price = _reprice_tp_outside_market(
                position.side, position.entry_price, current_price,
                atr_val, tick_size, self._TP_ATR_MULTIPLIER,
            )
            tp_price = exec_client.format_price(
                position.symbol, tp_price, ceil=(position.side == Side.LONG),
            )
            repriced = True
            logger.warning(
                "[TP_SYNC_REPRICED] market=%.4f → repriced tp=%.4f", current_price, tp_price,
            )

        try:
            tp_id = _generate_client_order_id("bfat_restore_tp")
            tp_resp = exec_client.place_take_profit_market_order(
                position.symbol, position.side, position.size, tp_price, tp_id,
            )
            algo_id = str(tp_resp.get("orderId", ""))
            if algo_id:
                self._engine._current_tp_algo_id = algo_id
                self._engine._current_take_profit = tp_price
                self._engine._tp_status = "repriced" if repriced else "exchange"
                self._engine._tp_last_error = None
                sm = self._engine._state_machine
                try:
                    sm.on_take_profit_update(tp_price)
                except ValueError:
                    pass
                logger.info(
                    "TP recovered for restored position: %s @ %.2f (ATR×%.1f, repriced=%s)",
                    algo_id, tp_price, self._TP_ATR_MULTIPLIER, repriced,
                )
        except Exception as e:
            self._engine._tp_status = "failed"
            self._engine._tp_last_error = str(e)
            logger.warning("TP recovery during sync failed (non-fatal): %s", e)

    async def _run(self) -> None:
        """Background: start streams and run until stopped."""
        self._critical_error = None
        _, _, system_log_repo = create_persistence(self._db)
        try:
            self._engine = self._build_engine()
            self._sync_binance_position()
            market = BinanceMarketStream(
                symbol=self._settings.bfat_symbol,
                engine=self._engine,
                equity_provider=self._equity_provider,
                testnet=self._settings.binance_testnet,
            )
            user = BinanceUserStream(
                api_key=self._settings.binance_api_key,
                api_secret=self._settings.binance_api_secret,
                engine=self._engine,
                equity_provider=self._equity_provider,
                testnet=self._settings.binance_testnet,
            )
            self._market_stream = market
            self._user_stream = user
            await market.start()
            await user.start()
            system_log_repo.insert(
                level="INFO",
                event="engine_started",
                message="Market and user streams connected. Engine running.",
            )
            while self._running:
                await asyncio.sleep(1)
            system_log_repo.insert(
                level="INFO",
                event="engine_stopped",
                message="Engine stopped. Streams disconnected.",
            )
            await market.stop()
            await user.stop()
        except Exception as e:
            self._critical_error = str(e)
            system_log_repo.insert(
                level="ERROR",
                event="engine_start_failed",
                message=f"Engine failed: {e}",
            )
            if self._market_stream:
                try:
                    await self._market_stream.stop()
                except Exception:
                    pass
            if self._user_stream:
                try:
                    await self._user_stream.stop()
                except Exception:
                    pass
        finally:
            self._running = False
            self._market_stream = None
            self._user_stream = None
            self._engine = None

    async def start(self) -> None:
        """Start engine in background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop engine. Awaits clean shutdown of streams."""
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=30.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    def get_status(self) -> dict[str, Any]:
        """Return current status."""
        return self._get_status()

    def _get_trade_repo(self):
        if self._engine is not None:
            repo = getattr(self._engine, "_trade_repo", None)
            if repo is not None:
                return repo
        repo, _, _ = create_persistence(self._db)
        return repo

    @staticmethod
    def _safe_float(val: Any, default: float = 0.0) -> float:
        """Safely convert DB value to float. Handles str, None, non-numeric."""
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _holding_duration(entry_ts: Any, exit_ts: Any) -> tuple[int, str]:
        """Return (seconds, readable) for holding duration between ISO timestamps."""
        entry_ts = str(entry_ts) if entry_ts is not None else ""
        exit_ts = str(exit_ts) if exit_ts is not None else ""
        if not entry_ts or not exit_ts:
            return 0, "–"
        try:
            fmt1 = "%Y-%m-%dT%H:%M:%SZ"
            fmt2 = "%Y-%m-%dT%H:%M:%S"
            t_entry = None
            for fmt in (fmt1, fmt2):
                try:
                    t_entry = datetime.strptime(entry_ts, fmt)
                    break
                except ValueError:
                    pass
            t_exit = None
            for fmt in (fmt1, fmt2):
                try:
                    t_exit = datetime.strptime(exit_ts, fmt)
                    break
                except ValueError:
                    pass
            if t_entry is None or t_exit is None:
                return 0, "–"
            secs = max(0, int((t_exit - t_entry).total_seconds()))
            h, rem = divmod(secs, 3600)
            m, _ = divmod(rem, 60)
            readable = f"{h}h {m}m" if h > 0 else f"{m}m"
            return secs, readable
        except Exception:
            return 0, "–"

    def get_trades(self, symbol: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return closed trades with computed display fields from DB only."""
        sf = self._safe_float
        rows = self._get_trade_repo().query(symbol=symbol, limit=limit, offset=offset)
        result: list[dict[str, Any]] = []
        for r in rows:
            entry_price = sf(r.get("entry_price"))
            exit_price = sf(r.get("exit_price"))
            side = (str(r.get("side") or "")).upper()
            pnl_pct = 0.0
            if entry_price > 0:
                if side == "LONG":
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                elif side == "SHORT":
                    pnl_pct = (entry_price - exit_price) / entry_price * 100
            dur_secs, dur_readable = self._holding_duration(
                r.get("entry_time"), r.get("exit_time"),
            )
            result.append({
                "id": r.get("id"),
                "symbol": r.get("symbol"),
                "side": r.get("side"),
                "entry_time": r.get("entry_time") or "",
                "entry_price": entry_price,
                "exit_time": r.get("exit_time") or "",
                "exit_price": exit_price,
                "size": sf(r.get("size")),
                "initial_stop_price": sf(r.get("initial_stop_price")),
                "pnl": sf(r.get("pnl")),
                "gross_pnl": sf(r.get("gross_pnl")),
                "net_pnl": sf(r.get("net_pnl")),
                "initial_risk": sf(r.get("initial_risk")),
                "r_multiple": sf(r.get("pnl_r")) if r.get("pnl_r") is not None else None,
                "risk_reward_ratio": sf(r.get("pnl_r")) if r.get("pnl_r") is not None else None,
                "r_validation_status": r.get("r_validation_status") or "",
                "trade_hash": r.get("trade_hash") or "",
                "stop_phase": r.get("stop_phase") or "",
                "signal_candle_ts": r.get("signal_candle_ts") or "",
                "pnl_percent": round(pnl_pct, 4),
                "holding_duration_seconds": dur_secs,
                "holding_duration_readable": dur_readable,
            })
        return result

    def get_trade_summary(self, symbol: str) -> dict[str, Any]:
        """Compute summary performance metrics from all closed trades."""
        sf = self._safe_float
        rows = self._get_trade_repo().query(symbol=symbol, limit=10000)
        total = len(rows)
        empty_summary: dict[str, Any] = {
            "total_trades": 0,
            "win_rate": 0.0,
            "average_r": 0.0,
            "expectancy_r": 0.0,
            "total_net_pnl": 0.0,
            "max_drawdown_r": 0.0,
            "best_trade_r": 0.0,
            "worst_trade_r": 0.0,
        }
        if total == 0:
            return empty_summary
        r_values: list[float] = []
        net_pnls: list[float] = []
        wins = 0
        for r in rows:
            raw_r = r.get("pnl_r")
            if raw_r is not None:
                rv = sf(raw_r)
                r_values.append(rv)
                if rv > 0:
                    wins += 1
            np_raw = r.get("net_pnl")
            if np_raw is None:
                np_raw = r.get("pnl")
            net_pnls.append(sf(np_raw))
        avg_r = sum(r_values) / len(r_values) if r_values else 0.0
        win_rate = (wins / len(r_values) * 100) if r_values else 0.0
        win_rs = [v for v in r_values if v > 0]
        lose_rs = [v for v in r_values if v <= 0]
        avg_win = sum(win_rs) / len(win_rs) if win_rs else 0.0
        avg_loss = sum(lose_rs) / len(lose_rs) if lose_rs else 0.0
        wr = win_rate / 100
        expectancy = wr * avg_win + (1 - wr) * avg_loss
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for rv in r_values:
            cumulative += rv
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return {
            "total_trades": total,
            "win_rate": round(win_rate, 2),
            "average_r": round(avg_r, 4),
            "expectancy_r": round(expectancy, 4),
            "total_net_pnl": round(sum(net_pnls), 4),
            "max_drawdown_r": round(max_dd, 4),
            "best_trade_r": round(max(r_values), 4) if r_values else 0.0,
            "worst_trade_r": round(min(r_values), 4) if r_values else 0.0,
        }

    def get_insight(self) -> dict[str, Any]:
        """Return last strategy evaluation + regime classifier data for insight API."""
        default: dict[str, Any] = {
            "regime": "Unknown",
            "active_strategy": "Unknown",
            "regime_changed": False,
            "volatility_score": 0.0,
            "atr_value": 0.0,
            "volume_ratio": 0.0,
            "ema_fast": 0.0,
            "ema_slow": 0.0,
            "engine_reasoning": ["No evaluation yet. Start the engine to receive insights."],
        }
        if self._engine is None:
            return default
        se = getattr(self._engine, "_strategy_engine", None)
        if se is None:
            return default
        details = getattr(se, "get_last_evaluation_details", lambda: {})()
        if not details:
            return default

        result: dict[str, Any] = {
            "regime": details.get("regime", "Unknown"),
            "active_strategy": details.get("active_strategy", "Unknown"),
            "regime_changed": details.get("regime_changed", False),
            "regime_score": details.get("regime_score", 0),
            "position_scale": details.get("position_scale", 1.0),
            "cooldown_remaining": details.get("cooldown_remaining", 0),
            "engine_reasoning": details.get("engine_reasoning", []),
            "volatility_score": details.get("volatility_score", 0.0),
            "atr_value": details.get("atr_value", 0.0),
            "volume_ratio": details.get("volume_ratio", 0.0),
            "ema_fast": details.get("ema_fast", 0.0),
            "ema_slow": details.get("ema_slow", 0.0),
        }
        for k in ("rsi", "range_high", "range_low", "range_mid", "volume_zscore", "close_price"):
            if k in details:
                result[k] = details[k]
        rc_details = details.get("regime_classifier")
        if rc_details:
            result["regime_classifier"] = rc_details
        if "trend_reference" in details:
            result["trend_reference"] = details["trend_reference"]
        if "range_reference" in details:
            result["range_reference"] = details["range_reference"]
        if "entry_conditions" in details:
            result["entry_conditions"] = details["entry_conditions"]
        skip_reason = details.get("skip_reason")
        if skip_reason is None and self._engine is not None:
            skip_reason = getattr(self._engine, "_last_skip_reason", None)
        result["skip_reason"] = skip_reason
        result["last_insight_update_ts"] = details.get("last_insight_update_ts", 0)
        entry_snapshot = getattr(self._engine, "_entry_insight_snapshot", None)
        if entry_snapshot:
            result["entry_insight"] = entry_snapshot
        return result
