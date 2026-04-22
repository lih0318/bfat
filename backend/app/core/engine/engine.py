"""BFAT orchestrator. No business logic. Coordinates modules only."""

import hashlib
import logging
import time
from datetime import datetime
from decimal import Decimal, getcontext
from typing import Any

from app.core.execution import BinanceExecutionClient, _generate_client_order_id

logger = logging.getLogger(__name__)
from app.domain.enums import PositionState, Side, StopPhase
from app.domain.position import Position
from app.domain.signal import CloseSignal, Signal


class CancelFailureError(RuntimeError):
    """Raised when cancel fails after new stop placed. Do not flatten."""


class NewStopPlacementError(RuntimeError):
    """Raised when new stop placement fails. Old stop remains. Do not flatten."""


def _validate_response_dict(resp: Any) -> dict:
    """Ensure response is a dict. Raises if invalid."""
    if not isinstance(resp, dict):
        raise RuntimeError(f"Execution response not dict: {type(resp).__name__}")
    return resp


def _validate_market_response(resp: dict) -> None:
    """Validate market order response has required keys."""
    for key in ("status", "executedQty"):
        if key not in resp:
            raise RuntimeError(f"Market response missing required key: {key}")
    if float(resp.get("executedQty", 0)) <= 0:
        raise RuntimeError("Market response executedQty <= 0")
    if resp.get("avgPrice") is None and not resp.get("fills"):
        raise RuntimeError("Market response missing avgPrice or fills")


def _validate_stop_response_keys(resp: dict) -> None:
    """Validate stop order response has orderId."""
    if "orderId" not in resp:
        raise RuntimeError("Stop response missing orderId")


def _validate_cancel_response(resp: dict) -> None:
    """Validate cancel response has orderId."""
    if "orderId" not in resp:
        raise RuntimeError("Cancel response missing orderId")


def _parse_fill(resp: dict) -> tuple[float, float]:
    """Extract entry_price and executed_qty from market order response. Raises if invalid."""
    status = resp.get("status")
    if status != "FILLED":
        raise RuntimeError(f"Market order not FILLED: status={status}")
    exec_qty = float(resp.get("executedQty", 0))
    if exec_qty <= 0:
        raise RuntimeError(f"Market order has no executedQty: {resp}")
    avg = resp.get("avgPrice")
    if avg is not None:
        price = float(avg)
    else:
        fills = resp.get("fills") or []
        if not fills:
            raise RuntimeError("Market order FILLED but no avgPrice or fills")
        total_qty = 0.0
        total_value = 0.0
        for f in fills:
            qty = float(f.get("qty", 0))
            px = float(f.get("price", 0))
            total_qty += qty
            total_value += qty * px
        price = total_value / total_qty if total_qty > 0 else 0.0
    if price <= 0:
        raise RuntimeError(f"Invalid fill price: {price}")
    return price, exec_qty


def _validate_stop_response(resp: Any) -> str:
    """Validate stop order response. Returns orderId string."""
    resp = _validate_response_dict(resp)
    _validate_stop_response_keys(resp)
    return str(resp["orderId"])


def _close_position_flatten(
    execution: BinanceExecutionClient,
    symbol: str,
    side: Side,
    size: float,
) -> None:
    """Place market order to flatten. Validates FILLED, executedQty>=size, avgPrice>0."""
    close_side = Side.SHORT if side == Side.LONG else Side.LONG
    resp = execution.place_market_order(
        symbol, close_side, size, _generate_client_order_id("bfat_flatten")
    )
    resp = _validate_response_dict(resp)
    _validate_market_response(resp)
    try:
        price, exec_qty = _parse_fill(resp)
    except RuntimeError as e:
        raise RuntimeError("CRITICAL FLATTEN FAILURE") from e
    if exec_qty < size:
        raise RuntimeError(
            f"CRITICAL FLATTEN FAILURE: executedQty {exec_qty} < position size {size}"
        )
    if price <= 0:
        raise RuntimeError("CRITICAL FLATTEN FAILURE: avgPrice <= 0")


ATR_PERIOD = 14
INITIAL_STOP_ATR = 1.6


def _atr(candles: list[dict], period: int = 14) -> float:
    """Compute ATR for last candle. Returns 0 if insufficient data."""
    if len(candles) < period + 1:
        return 0.0
    tr_list: list[float] = [0.0]
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period


FALLBACK_STOP_PCT = 0.015


def _validate_stop_price(
    side: Side, stop_price: float, actual_entry_price: float, atr_val: float,
) -> float:
    """Ensure stop_price is on the correct side of actual_entry_price.

    Recalculates from actual_entry_price if the strategy-provided stop
    became invalid due to slippage.
    """
    valid = (
        stop_price < actual_entry_price
        if side == Side.LONG
        else stop_price > actual_entry_price
    )
    if valid:
        return stop_price
    logger.warning(
        "[SL_REPRICE] side=%s entry=%.4f invalid_stop=%.4f → recalculating",
        side.value, actual_entry_price, stop_price,
    )
    if atr_val > 0:
        if side == Side.LONG:
            return actual_entry_price - INITIAL_STOP_ATR * atr_val
        return actual_entry_price + INITIAL_STOP_ATR * atr_val
    if side == Side.LONG:
        return actual_entry_price * (1 - FALLBACK_STOP_PCT)
    return actual_entry_price * (1 + FALLBACK_STOP_PCT)


_TP_BUFFER_TICKS = 5  # minimum tick-size buffer between market price and TP


def _validate_take_profit_price(
    side: Side,
    candidate_tp: float,
    actual_entry_price: float,
    current_market_price: float,
) -> bool:
    """Return True if candidate TP is on the correct side of BOTH entry and market."""
    if side == Side.LONG:
        return candidate_tp > actual_entry_price and candidate_tp > current_market_price
    return candidate_tp < actual_entry_price and candidate_tp < current_market_price


def _reprice_tp_outside_market(
    side: Side,
    actual_entry_price: float,
    current_market_price: float,
    atr_val: float,
    tick_size: float,
    tp_atr_mult: float = 2.8,
) -> float:
    """Compute a TP safely outside the current market price.

    Uses the farther of (entry-based ATR TP) and (market + buffer) so the
    resulting trigger never causes an immediate-trigger rejection.
    """
    buffer = max(tick_size * _TP_BUFFER_TICKS, atr_val * 0.3) if atr_val > 0 else tick_size * _TP_BUFFER_TICKS
    if side == Side.LONG:
        atr_tp = actual_entry_price + tp_atr_mult * atr_val if atr_val > 0 else 0
        market_tp = current_market_price + buffer
        return max(atr_tp, market_tp) if atr_tp > 0 else market_tp
    atr_tp = actual_entry_price - tp_atr_mult * atr_val if atr_val > 0 else 0
    market_tp = current_market_price - buffer
    return min(atr_tp, market_tp) if atr_tp > 0 else market_tp


def _ts() -> str:
    """ISO timestamp."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


getcontext().prec = 28


class BFATEngine:
    """Orchestrates strategy, risk, execution, state machine, persistence."""

    def __init__(
        self,
        strategy_engine: Any,
        risk_manager: Any,
        kill_switch: Any,
        execution_client: BinanceExecutionClient,
        state_machine: Any,
        trade_repository: Any,
        equity_repository: Any,
        system_log_repository: Any,
        symbol: str,
        notifier: Any | None = None,
    ) -> None:
        self._strategy_engine = strategy_engine
        self._risk_manager = risk_manager
        self._kill_switch = kill_switch
        self._execution = execution_client
        self._state_machine = state_machine
        self._trade_repo = trade_repository
        self._equity_repo = equity_repository
        self._system_log = system_log_repository
        self._symbol = symbol
        self._notifier = notifier
        self._current_stop_order_id: str | None = None
        self._sl_verified: bool = False
        self._current_tp_algo_id: str | None = None
        self._current_take_profit: float | None = None
        self._tp_status: str = "none"  # none | exchange | fallback | failed | repriced
        self._tp_last_error: str | None = None
        self._last_signal_candle_ts: str = ""
        self._last_intrabar_entry_bucket_ts: str = ""
        self._last_close_candle_ts: str = ""
        self._last_skip_reason: str | None = None
        self._pending_fallback_close: bool = False
        self._entry_insight_snapshot: dict | None = None
        self._last_sl_recover_ts: float = 0.0
        self._post_close_cooldown_remaining: int = 0

    _SL_RECOVER_COOLDOWN = 30.0
    POST_CLOSE_COOLDOWN_CANDLES = 3

    def _check_state_consistency(self) -> None:
        """Raise if engine state is inconsistent."""
        state = self._state_machine.state
        pos = self._state_machine.position
        if state == PositionState.OPEN:
            if pos is None:
                raise RuntimeError("Inconsistent state: OPEN without position")
            if self._current_stop_order_id is None:
                logger.warning("[STATE_WARN] OPEN without _current_stop_order_id; SL recovery needed")
        if state == PositionState.FLAT:
            if pos is not None:
                raise RuntimeError("Inconsistent state: FLAT with position")
            if self._current_stop_order_id is not None:
                raise RuntimeError("Invariant: FLAT must have _current_stop_order_id None")
            if self._current_tp_algo_id is not None:
                raise RuntimeError("Invariant: FLAT must have _current_tp_algo_id None")

    def _try_recover_sl(self, candles: list[dict]) -> None:
        """Attempt to place a missing SL order with throttling."""
        now = time.monotonic()
        if now - self._last_sl_recover_ts < self._SL_RECOVER_COOLDOWN:
            return
        self._last_sl_recover_ts = now

        pos = self._state_machine.position
        if pos is None:
            return
        atr_val = _atr(candles, ATR_PERIOD)
        stop_price = _validate_stop_price(
            pos.side, pos.stop_price, pos.entry_price, atr_val,
        )
        try:
            sl_id, final_stop = self._place_stop_with_retry(
                pos.side, pos.size, stop_price, pos.entry_price, atr_val,
            )
            self._current_stop_order_id = sl_id
            self._sl_verified = True
            if final_stop != pos.stop_price:
                try:
                    self._state_machine.on_stop_update(pos.stop_phase, final_stop)
                except ValueError:
                    pass
            logger.info("[SL_RECOVERED] id=%s stop=%.4f", sl_id, final_stop)
            self._system_log.insert(
                level="INFO", event="sl_recovered",
                message=f"SL recovered: {sl_id} @ {final_stop:.4f}",
            )
        except Exception as e:
            logger.warning("[SL_RECOVER_FAILED] err=%s; will retry in %.0fs", e, self._SL_RECOVER_COOLDOWN)

    def _cleanup_stale_algo_orders(self) -> None:
        """Cancel any lingering SL/TP algo orders before new entry."""
        try:
            count = self._execution.cancel_all_algo_orders(self._symbol)
            if count > 0:
                logger.info("[STALE_ORDERS_CLEANED] cancelled=%d", count)
        except Exception as e:
            logger.warning("[STALE_ORDER_CLEANUP_FAILED] err=%s", e)

    def _place_stop_with_retry(
        self,
        side: Side,
        actual_size: float,
        stop_price: float,
        actual_entry_price: float,
        atr_val: float,
        *,
        verify: bool = True,
    ) -> tuple[str, float]:
        """Place SL with one retry using a fallback price. Returns (orderId, final_stop_price).

        Attempt 1: the validated stop_price.
        Attempt 2: a percentage-based fallback derived from actual_entry_price.
        When verify=True, confirms the order is live on exchange via backoff query.
        Raises RuntimeError only if both attempts fail or verification fails.
        """
        stop_id = _generate_client_order_id("bfat_stop")
        try:
            stop_resp = self._execution.place_stop_market_order(
                self._symbol, side, actual_size, stop_price, stop_id,
            )
            if stop_resp and "orderId" in stop_resp:
                order_id = _validate_stop_response(stop_resp)
                if verify:
                    if not self._execution.verify_algo_order_with_backoff(self._symbol, order_id):
                        logger.warning("[SL_PLACED_BUT_NOT_VERIFIED] id=%s price=%.4f", order_id, stop_price)
                        raise RuntimeError(f"SL placed (id={order_id}) but not confirmed on exchange")
                logger.info("[STOP_ORDER_PLACED] id=%s price=%.4f verified=%s", order_id, stop_price, verify)
                return order_id, stop_price
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("[STOP_ATTEMPT_1_FAILED] price=%.4f err=%s", stop_price, e)

        if side == Side.LONG:
            fallback_stop = actual_entry_price * (1 - FALLBACK_STOP_PCT)
        else:
            fallback_stop = actual_entry_price * (1 + FALLBACK_STOP_PCT)
        fallback_stop = self._execution.format_price(
            self._symbol, fallback_stop, ceil=(side == Side.SHORT),
        )

        if abs(fallback_stop - stop_price) < 1e-8:
            raise RuntimeError(
                f"SL placement failed and fallback price identical ({stop_price})"
            )

        retry_id = _generate_client_order_id("bfat_stop_retry")
        try:
            stop_resp = self._execution.place_stop_market_order(
                self._symbol, side, actual_size, fallback_stop, retry_id,
            )
            if stop_resp and "orderId" in stop_resp:
                order_id = _validate_stop_response(stop_resp)
                if verify:
                    if not self._execution.verify_algo_order_with_backoff(self._symbol, order_id):
                        logger.warning("[SL_RETRY_PLACED_BUT_NOT_VERIFIED] id=%s price=%.4f", order_id, fallback_stop)
                        raise RuntimeError(f"SL retry placed (id={order_id}) but not confirmed on exchange")
                logger.info("[STOP_ORDER_PLACED_RETRY] id=%s price=%.4f verified=%s", order_id, fallback_stop, verify)
                return order_id, fallback_stop
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("[STOP_ATTEMPT_2_FAILED] price=%.4f err=%s", fallback_stop, e)

        raise RuntimeError(
            f"SL placement failed after 2 attempts "
            f"(original={stop_price}, fallback={fallback_stop})"
        )

    def _place_tp_with_retry(
        self, side: Side, actual_size: float, take_profit: float, *, verify: bool = True,
    ) -> str:
        """Place TP with one retry. Returns orderId. Raises RuntimeError if both fail or verification fails."""
        tp_id = _generate_client_order_id("bfat_tp")
        try:
            resp = self._execution.place_take_profit_market_order(
                self._symbol, side, actual_size, take_profit, tp_id,
            )
            if resp and "orderId" in resp:
                order_id = _validate_stop_response(resp)
                if verify:
                    if not self._execution.verify_algo_order_with_backoff(self._symbol, order_id):
                        logger.warning("[TP_PLACED_BUT_NOT_VERIFIED] id=%s tp=%.4f", order_id, take_profit)
                        raise RuntimeError(f"TP placed (id={order_id}) but not confirmed on exchange")
                return order_id
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("[TP_ATTEMPT_1_FAILED] tp=%.4f err=%s", take_profit, e)

        retry_id = _generate_client_order_id("bfat_tp_retry")
        try:
            resp = self._execution.place_take_profit_market_order(
                self._symbol, side, actual_size, take_profit, retry_id,
            )
            if resp and "orderId" in resp:
                order_id = _validate_stop_response(resp)
                if verify:
                    if not self._execution.verify_algo_order_with_backoff(self._symbol, order_id):
                        logger.warning("[TP_RETRY_PLACED_BUT_NOT_VERIFIED] id=%s tp=%.4f", order_id, take_profit)
                        raise RuntimeError(f"TP retry placed (id={order_id}) but not confirmed on exchange")
                return order_id
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("[TP_ATTEMPT_2_FAILED] tp=%.4f err=%s", take_profit, e)

        raise RuntimeError(f"TP placement failed after 2 attempts (tp={take_profit})")

    def _validate_and_place_tp(
        self,
        side: Side,
        actual_size: float,
        candidate_tp: float,
        actual_entry_price: float,
        atr_val: float,
        *,
        fail_closed: bool = True,
    ) -> tuple[str | None, float]:
        """Validate TP price against current market, reprice if needed, then place.

        When fail_closed=True (default for entry), raises on failure instead of
        returning None. When fail_closed=False (runtime recovery), returns
        (None, tp_price) on failure so the engine can use fallback TP.
        """
        current_price = self._execution.get_ticker_price(self._symbol)
        if current_price <= 0:
            current_price = actual_entry_price

        tp_price = candidate_tp
        repriced = False
        if not _validate_take_profit_price(side, tp_price, actual_entry_price, current_price):
            filters = self._execution._get_filters(self._symbol)
            tick_size = filters.get("price_step", 0.1)
            tp_price = _reprice_tp_outside_market(
                side, actual_entry_price, current_price, atr_val, tick_size,
            )
            tp_price = self._execution.format_price(
                self._symbol, tp_price, ceil=(side == Side.LONG),
            )
            repriced = True
            logger.warning(
                "[TP_REPRICED] original=%.4f → repriced=%.4f market=%.4f",
                candidate_tp, tp_price, current_price,
            )

        try:
            algo_id = self._place_tp_with_retry(side, actual_size, tp_price)
            self._tp_status = "repriced" if repriced else "exchange"
            self._tp_last_error = None
            logger.info("[TP_ORDER_PLACED] id=%s tp=%.4f repriced=%s verified=True", algo_id, tp_price, repriced)
            return algo_id, tp_price
        except Exception as e:
            self._tp_status = "failed"
            self._tp_last_error = str(e)
            if fail_closed:
                logger.error("[TP_REGISTRATION_FAILED_CLOSED] err=%s; entry will be aborted", e)
                self._system_log.insert(
                    level="ERROR",
                    event="tp_registration_failed_closed",
                    message=f"TP failed (fail-closed): {e}. tp={tp_price}",
                )
                raise RuntimeError(f"TP registration failed (fail-closed): {e}") from e
            logger.warning("[TP_REGISTRATION_FAILED] err=%s; fallback TP active", e)
            self._system_log.insert(
                level="WARNING",
                event="tp_registration_failed",
                message=f"TP failed: {e}. Fallback TP active. tp={tp_price}",
            )
            return None, tp_price

    def _check_realtime_tp(self, live_bar: dict, equity: float) -> None:
        """Check TP in real-time for OPEN positions without exchange TP algo."""
        pos = self._state_machine.position
        if pos is None:
            return
        tp = pos.take_profit
        if tp is None or self._current_tp_algo_id:
            return
        live_price = live_bar.get("close", 0)
        if live_price <= 0:
            return
        hit = (
            (pos.side == Side.LONG and live_price >= tp)
            or (pos.side == Side.SHORT and live_price <= tp)
        )
        if hit:
            try:
                self._market_close_open_position(
                    equity,
                    event="take_profit_hit",
                    message=f"Take profit (realtime) price={live_price:.4f} target={tp:.4f}",
                )
            except Exception as e:
                self._system_log.insert(
                    level="ERROR",
                    event="realtime_tp_close_failed",
                    message=str(e),
                )

    def evaluate_for_insight(self, candles: list[dict]) -> None:
        """Run strategy evaluation to populate Insight only. No orders, no position changes."""
        self._strategy_engine.evaluate_for_insight(candles)

    def evaluate_for_insight_live(self, candles: list[dict], live_bar: dict) -> None:
        """Update Insight with forming bar data. No orders, no position changes."""
        self._strategy_engine.evaluate_for_insight_live(candles, live_bar)

    def on_market_update(
        self, candles: list[dict], live_bar: dict, equity: float,
    ) -> None:
        """Handle a forming-candle update for Range intrabar entry.

        Only attempts entry when FLAT + no same-bucket attempt already made.
        The closed-candle `on_candle_close` path is never touched here.
        """
        try:
            self._check_state_consistency()
        except RuntimeError:
            return
        if self._state_machine.state == PositionState.OPEN:
            if self._current_stop_order_id is None:
                self._try_recover_sl(candles)
            self._check_realtime_tp(live_bar, equity)
            return
        if self._state_machine.state != PositionState.FLAT:
            return
        if self._post_close_cooldown_remaining > 0:
            self._last_skip_reason = f"post_close_cooldown ({self._post_close_cooldown_remaining} remaining)"
            return
        if self._kill_switch.is_triggered():
            self._last_skip_reason = "kill_switch_triggered"
            return
        bucket_ts = live_bar.get("timestamp", "")
        if bucket_ts and bucket_ts == self._last_intrabar_entry_bucket_ts:
            self._last_skip_reason = "already_attempted_this_bar"
            return
        if bucket_ts and bucket_ts == self._last_signal_candle_ts:
            self._last_skip_reason = "signal_already_used_this_bar"
            return
        if bucket_ts and bucket_ts == self._last_close_candle_ts:
            self._last_skip_reason = "close_first_wait_next_cycle"
            return

        signal = self._strategy_engine.evaluate_ranging_intrabar(
            candles, live_bar, self._state_machine.position,
        )
        if signal is None:
            return

        logger.info(
            "[INTRABAR_SIGNAL] side=%s bucket=%s", signal.side.value, bucket_ts,
        )

        atr_val = _atr(candles, ATR_PERIOD)
        entry_price_est = live_bar["close"]

        if signal.stop_price is not None:
            stop_price_est = signal.stop_price
        else:
            if atr_val <= 0:
                self._last_skip_reason = "atr_invalid"
                return
            stop_price_est = (
                entry_price_est - INITIAL_STOP_ATR * atr_val
                if signal.side == Side.LONG
                else entry_price_est + INITIAL_STOP_ATR * atr_val
            )
        position_size = self._risk_manager.calculate_position_size(
            equity, entry_price_est, stop_price_est,
        )
        position_size *= signal.position_scale
        if position_size <= 0:
            self._last_skip_reason = "position_size_zero"
            return

        try:
            self._state_machine.on_signal(signal)
            self._cleanup_stale_algo_orders()
            entry_id = _generate_client_order_id("bfat_intra")
            mkt_resp = _validate_response_dict(
                self._execution.place_market_order(
                    self._symbol, signal.side, position_size, entry_id,
                )
            )
            status = mkt_resp.get("status")
            if status != "FILLED":
                self._state_machine.rollback_entry()
                raise RuntimeError(f"Intrabar entry not FILLED: status={status}")
            try:
                _validate_market_response(mkt_resp)
                actual_entry_price, actual_size = _parse_fill(mkt_resp)
            except RuntimeError as e:
                flatten_size = float(mkt_resp.get("executedQty", 0)) or position_size
                _close_position_flatten(self._execution, self._symbol, signal.side, flatten_size)
                self._state_machine.rollback_entry()
                raise RuntimeError("Intrabar entry response invalid; flattened") from e

            stop_price = signal.stop_price if signal.stop_price is not None else (
                actual_entry_price - INITIAL_STOP_ATR * atr_val
                if signal.side == Side.LONG
                else actual_entry_price + INITIAL_STOP_ATR * atr_val
            )
            stop_price = _validate_stop_price(signal.side, stop_price, actual_entry_price, atr_val)
            try:
                sl_order_id, stop_price = self._place_stop_with_retry(
                    signal.side, actual_size, stop_price, actual_entry_price, atr_val,
                )
                self._current_stop_order_id = sl_order_id
                self._sl_verified = True
                logger.info("[SL_ORDER_PLACED] id=%s stop=%.4f", sl_order_id, stop_price)
            except Exception as e:
                _close_position_flatten(self._execution, self._symbol, signal.side, actual_size)
                self._state_machine.rollback_entry()
                raise RuntimeError("Stop failed after intrabar entry; flattened") from e

            tp_algo_id: str | None = None
            final_tp = signal.take_profit
            if signal.take_profit is not None and float(signal.take_profit) > 0:
                try:
                    tp_algo_id, final_tp = self._validate_and_place_tp(
                        signal.side, actual_size, float(signal.take_profit),
                        actual_entry_price, atr_val,
                        fail_closed=True,
                    )
                except Exception as e:
                    logger.error("[TP_FAIL_CLOSED_INTRABAR] err=%s; cancelling SL and flattening", e)
                    try:
                        self._execution.cancel_order(self._symbol, self._current_stop_order_id)
                    except Exception:
                        pass
                    self._current_stop_order_id = None
                    self._sl_verified = False
                    _close_position_flatten(self._execution, self._symbol, signal.side, actual_size)
                    self._state_machine.rollback_entry()
                    raise RuntimeError("TP failed on intrabar entry (fail-closed); flattened") from e
            else:
                self._tp_status = "none"
                self._tp_last_error = None

            position = Position(
                symbol=self._symbol,
                side=signal.side,
                size=actual_size,
                entry_price=actual_entry_price,
                stop_price=stop_price,
                initial_stop_price=stop_price,
                stop_phase=StopPhase.INITIAL,
                entry_time=_ts(),
                correlation_id=entry_id,
                take_profit=final_tp,
            )
            try:
                self._state_machine.on_entry_filled(position)
            except Exception as e:
                if tp_algo_id:
                    try:
                        self._execution.cancel_order(self._symbol, tp_algo_id)
                    except Exception:
                        pass
                if self._current_stop_order_id:
                    try:
                        self._execution.cancel_order(self._symbol, self._current_stop_order_id)
                    except Exception:
                        pass
                _close_position_flatten(self._execution, self._symbol, signal.side, actual_size)
                self._state_machine.rollback_entry()
                raise RuntimeError("State transition failed after intrabar entry; flattened") from e

            self._current_tp_algo_id = tp_algo_id
            self._last_signal_candle_ts = bucket_ts
            self._last_intrabar_entry_bucket_ts = bucket_ts
            self._current_take_profit = final_tp
            if self._notifier:
                self._notifier.notify_entry(position)
            try:
                snapshot = self._strategy_engine.get_last_evaluation_details()
                snapshot["snapshot_time"] = _ts()
                self._entry_insight_snapshot = snapshot
            except Exception:
                pass
            self._check_state_consistency()
            logger.info(
                "[INTRABAR_ENTRY_FILLED] side=%s price=%.4f size=%.6f bucket=%s",
                signal.side.value, actual_entry_price, actual_size, bucket_ts,
            )
        except RuntimeError:
            if self._state_machine.state == PositionState.ENTERING:
                self._state_machine.rollback_entry()
            raise
        except Exception as e:
            if self._state_machine.state == PositionState.ENTERING:
                self._state_machine.rollback_entry()
            raise RuntimeError("Intrabar entry failure") from e

    def on_candle_close(self, candles: list[dict], equity: float) -> None:
        """Handle candle close: evaluate strategy, entries, CloseSignal exits, logical TP."""
        self._last_skip_reason = None
        self._check_state_consistency()
        self._kill_switch.update_equity(equity)
        if self._kill_switch.is_triggered():
            self._last_skip_reason = "kill_switch_triggered"
            return
        if self._state_machine.state == PositionState.ENTERING:
            self._last_skip_reason = "state_entering"
            return

        # ── Fallback: detect position closed on exchange but not via User Stream ──
        if self._state_machine.state == PositionState.OPEN:
            pos = self._state_machine.position
            if pos is not None:
                try:
                    if self._is_position_flat_on_exchange():
                        if not self._pending_fallback_close:
                            self._pending_fallback_close = True
                            return
                        exit_price = self._resolve_exit_price_from_trades(pos)
                        if exit_price is None:
                            return
                        self._system_log.insert(
                            level="WARNING",
                            event="position_closed_fallback",
                            message=f"Fallback close detected. Exit: {exit_price}",
                        )
                        self._pending_fallback_close = False
                        self.on_position_closed(exit_price, equity)
                        return
                    else:
                        self._pending_fallback_close = False
                except Exception as e:
                    self._system_log.insert(
                        level="WARNING",
                        event="position_sync_check_failed",
                        message=str(e),
                    )

        # ── Strategy engine evaluation (regime + active strategy) ──
        result = self._strategy_engine.evaluate(
            candles, self._state_machine.position
        )

        # ── CloseSignal → market close (regime switch close-first, strategy exit, etc.) ──
        if isinstance(result, CloseSignal):
            if self._state_machine.state == PositionState.OPEN:
                try:
                    self._market_close_open_position(
                        equity,
                        event="close_signal",
                        message=result.reason,
                    )
                except Exception as e:
                    pos = self._state_machine.position
                    if pos is not None and self._is_position_flat_on_exchange():
                        exit_price = self._resolve_exit_price_from_trades(pos)
                        if exit_price is not None:
                            self._system_log.insert(
                                level="WARNING",
                                event="market_close_confirmed_after_sync",
                                message=f"Close signal raised error but position flat. Exit: {exit_price:.4f}",
                            )
                            self.on_position_closed(exit_price, equity)
                        else:
                            self._system_log.insert(
                                level="ERROR",
                                event="market_close_failed",
                                message=f"Close signal failed, position flat but no exit price from trades: {e}",
                            )
                    else:
                        self._system_log.insert(
                            level="ERROR",
                            event="market_close_failed",
                            message=f"Close signal failed: {e}. Will retry next candle.",
                        )
            is_close_first = "Close First" in result.reason
            self._last_skip_reason = (
                "close_first_wait_next_cycle" if is_close_first else "close_signal"
            )
            if is_close_first:
                self._last_close_candle_ts = candles[-1].get("timestamp", "")
            return

        # ── FLAT → entry if Signal ──
        if self._state_machine.state == PositionState.FLAT:
            if self._post_close_cooldown_remaining > 0:
                self._post_close_cooldown_remaining -= 1
                self._last_skip_reason = f"post_close_cooldown ({self._post_close_cooldown_remaining} remaining)"
                logger.info(
                    "[POST_CLOSE_COOLDOWN] %d candle(s) remaining before next entry allowed",
                    self._post_close_cooldown_remaining,
                )
                return
            if not isinstance(result, Signal):
                se_reason = getattr(self._strategy_engine, "_last_skip_reason", None)
                self._last_skip_reason = se_reason or "no_signal"
                return
            signal = result
            candle_ts = signal.signal_candle_ts
            if candle_ts == self._last_signal_candle_ts:
                self._last_skip_reason = "duplicate_signal_candle"
                return
            if candle_ts and candle_ts == self._last_close_candle_ts:
                self._last_skip_reason = "close_first_wait_next_cycle"
                return
            atr_val = _atr(candles, ATR_PERIOD)
            entry_price_est = candles[-1]["close"]

            if signal.stop_price is not None:
                stop_price_est = signal.stop_price
            else:
                if atr_val <= 0:
                    self._last_skip_reason = "atr_invalid"
                    return
                stop_price_est = (
                    entry_price_est - INITIAL_STOP_ATR * atr_val
                    if signal.side == Side.LONG
                    else entry_price_est + INITIAL_STOP_ATR * atr_val
                )
            position_size = self._risk_manager.calculate_position_size(
                equity, entry_price_est, stop_price_est
            )
            position_size *= signal.position_scale
            if position_size <= 0:
                self._last_skip_reason = "position_size_zero"
                return
            try:
                self._state_machine.on_signal(signal)
                self._cleanup_stale_algo_orders()
                entry_id = _generate_client_order_id("bfat_entry")
                mkt_resp = _validate_response_dict(
                    self._execution.place_market_order(
                        self._symbol,
                        signal.side,
                        position_size,
                        entry_id,
                    )
                )
                status = mkt_resp.get("status")
                if status != "FILLED":
                    self._state_machine.rollback_entry()
                    raise RuntimeError(f"Entry market order not FILLED: status={status}")
                try:
                    _validate_market_response(mkt_resp)
                    actual_entry_price, actual_size = _parse_fill(mkt_resp)
                except RuntimeError as e:
                    flatten_size = float(mkt_resp.get("executedQty", 0))
                    if flatten_size <= 0:
                        flatten_size = position_size
                    _close_position_flatten(
                        self._execution,
                        self._symbol,
                        signal.side,
                        flatten_size,
                    )
                    self._state_machine.rollback_entry()
                    raise RuntimeError("Entry market response invalid; position flattened") from e

                if signal.stop_price is not None:
                    stop_price = signal.stop_price
                else:
                    stop_price = (
                        actual_entry_price - INITIAL_STOP_ATR * atr_val
                        if signal.side == Side.LONG
                        else actual_entry_price + INITIAL_STOP_ATR * atr_val
                    )
                stop_price = _validate_stop_price(signal.side, stop_price, actual_entry_price, atr_val)
                try:
                    sl_order_id, stop_price = self._place_stop_with_retry(
                        signal.side, actual_size, stop_price, actual_entry_price, atr_val,
                    )
                    self._current_stop_order_id = sl_order_id
                    self._sl_verified = True
                    logger.info("[SL_ORDER_PLACED] id=%s stop=%.4f", sl_order_id, stop_price)
                except Exception as e:
                    _close_position_flatten(
                        self._execution,
                        self._symbol,
                        signal.side,
                        actual_size,
                    )
                    self._state_machine.rollback_entry()
                    raise RuntimeError(
                        "Stop order failed after entry filled; position flattened"
                    ) from e
                tp_algo_id: str | None = None
                final_tp = signal.take_profit
                if signal.take_profit is not None and float(signal.take_profit) > 0:
                    try:
                        tp_algo_id, final_tp = self._validate_and_place_tp(
                            signal.side, actual_size, float(signal.take_profit),
                            actual_entry_price, atr_val,
                            fail_closed=True,
                        )
                    except Exception as e:
                        logger.error("[TP_FAIL_CLOSED_ON_ENTRY] err=%s; cancelling SL and flattening", e)
                        try:
                            self._execution.cancel_order(self._symbol, self._current_stop_order_id)
                        except Exception:
                            pass
                        self._current_stop_order_id = None
                        self._sl_verified = False
                        _close_position_flatten(
                            self._execution, self._symbol, signal.side, actual_size,
                        )
                        self._state_machine.rollback_entry()
                        raise RuntimeError("TP failed on entry (fail-closed); position flattened") from e
                else:
                    self._tp_status = "none"
                    self._tp_last_error = None
                position = Position(
                    symbol=self._symbol,
                    side=signal.side,
                    size=actual_size,
                    entry_price=actual_entry_price,
                    stop_price=stop_price,
                    initial_stop_price=stop_price,
                    stop_phase=StopPhase.INITIAL,
                    entry_time=_ts(),
                    correlation_id=entry_id,
                    take_profit=final_tp,
                )
                try:
                    self._state_machine.on_entry_filled(position)
                except Exception as e:
                    if tp_algo_id:
                        try:
                            self._execution.cancel_order(self._symbol, tp_algo_id)
                        except Exception:
                            pass
                    if self._current_stop_order_id:
                        try:
                            self._execution.cancel_order(
                                self._symbol, self._current_stop_order_id
                            )
                        except Exception:
                            pass
                    _close_position_flatten(
                        self._execution,
                        self._symbol,
                        signal.side,
                        actual_size,
                    )
                    self._state_machine.rollback_entry()
                    raise RuntimeError(
                        "State transition failed after entry; position flattened"
                    ) from e
                self._current_tp_algo_id = tp_algo_id
                self._last_signal_candle_ts = signal.signal_candle_ts
                self._current_take_profit = final_tp
                if self._notifier:
                    self._notifier.notify_entry(position)
                try:
                    snapshot = self._strategy_engine.get_last_evaluation_details()
                    snapshot["snapshot_time"] = _ts()
                    self._entry_insight_snapshot = snapshot
                except Exception:
                    pass
                self._check_state_consistency()
            except RuntimeError as e:
                if self._state_machine.state == PositionState.ENTERING:
                    self._state_machine.rollback_entry()
                raise
            except Exception as e:
                if self._state_machine.state == PositionState.ENTERING:
                    self._state_machine.rollback_entry()
                raise RuntimeError("Entry failure") from e
            return

        # ── OPEN → deferred SL/TP health check, logical TP fallback.
        if self._state_machine.state == PositionState.OPEN:
            pos = self._state_machine.position
            if pos is None:
                return

            atr_val = _atr(candles, ATR_PERIOD)

            # -- Deferred SL health check --
            if self._current_stop_order_id:
                try:
                    sl_active = self._execution.verify_algo_order_active(
                        self._symbol, self._current_stop_order_id,
                    )
                except Exception as e:
                    logger.warning("[SL_VERIFY_API_FAILED] err=%s; assuming still active", e)
                    sl_active = True
                if not sl_active:
                    logger.warning("[SL_MISSING_ON_EXCHANGE] attempting re-registration")
                    self._system_log.insert(
                        level="WARNING", event="sl_missing",
                        message=f"SL order {self._current_stop_order_id} not found on exchange. Re-registering.",
                    )
                    try:
                        sl_id, _ = self._place_stop_with_retry(
                            pos.side, pos.size, pos.stop_price, pos.entry_price, atr_val,
                        )
                        self._current_stop_order_id = sl_id
                        self._sl_verified = True
                        logger.info("[SL_RE_REGISTERED] id=%s", sl_id)
                    except Exception as e:
                        logger.error("[SL_RE_REGISTRATION_FAILED] err=%s; flattening", e)
                        self._sl_verified = False
                        _close_position_flatten(
                            self._execution, self._symbol, pos.side, pos.size,
                        )
                        return
                else:
                    self._sl_verified = True
            else:
                logger.warning("[SL_ORDER_ID_MISSING] attempting emergency SL placement")
                try:
                    sl_id, new_stop = self._place_stop_with_retry(
                        pos.side, pos.size, pos.stop_price, pos.entry_price, atr_val,
                    )
                    self._current_stop_order_id = sl_id
                    self._sl_verified = True
                    if new_stop != pos.stop_price:
                        try:
                            self._state_machine.on_stop_update(pos.stop_phase, new_stop)
                        except ValueError:
                            pass
                    logger.info("[SL_EMERGENCY_PLACED] id=%s stop=%.4f", sl_id, new_stop)
                except Exception as e:
                    logger.error("[SL_EMERGENCY_FAILED] err=%s; flattening", e)
                    self._sl_verified = False
                    _close_position_flatten(
                        self._execution, self._symbol, pos.side, pos.size,
                    )
                    return

            # -- Deferred TP health check (TP should exist but algo_id missing) --
            tp = pos.take_profit
            if tp is not None and float(tp) > 0 and not self._current_tp_algo_id:
                logger.warning("[TP_MISSING_ON_EXCHANGE] attempting re-registration tp=%.4f", float(tp))
                algo_id, final_tp = self._validate_and_place_tp(
                    pos.side, pos.size, float(tp), pos.entry_price, atr_val,
                    fail_closed=False,
                )
                self._current_tp_algo_id = algo_id
                if final_tp != tp:
                    try:
                        self._state_machine.on_take_profit_update(final_tp)
                    except ValueError:
                        pass
                    self._current_take_profit = final_tp

            # -- TP recovery: calculate and place TP if missing --
            if pos.take_profit is None and atr_val > 0:
                active_regime = getattr(self._strategy_engine, "_active_regime", None)
                tp_price: float | None = None

                if active_regime == "RANGING":
                    rd = self._strategy_engine.range_strategy.get_last_evaluation_details()
                    range_mid = rd.get("range_mid")
                    if range_mid is not None and range_mid > 0:
                        tp_price = range_mid
                elif active_regime == "TRENDING":
                    _TP_ATR_MULT = 2.8
                    if pos.side == Side.LONG:
                        tp_price = pos.entry_price + _TP_ATR_MULT * atr_val
                    else:
                        tp_price = pos.entry_price - _TP_ATR_MULT * atr_val

                if tp_price is not None and tp_price > 0:
                    tp_price = self._execution.format_price(
                        self._symbol, tp_price, ceil=(pos.side == Side.LONG),
                    )
                    algo_id, final_tp = self._validate_and_place_tp(
                        pos.side, pos.size, tp_price, pos.entry_price, atr_val,
                        fail_closed=False,
                    )
                    self._current_tp_algo_id = algo_id
                    self._current_take_profit = final_tp
                    try:
                        self._state_machine.on_take_profit_update(final_tp)
                    except ValueError:
                        pass
                    logger.info("[TP_RECOVERED] regime=%s tp=%.4f algo=%s",
                                active_regime, final_tp, algo_id)

            # -- Logical candle-close TP fallback (TP set but no exchange algo) --
            if tp is not None and not self._current_tp_algo_id:
                close_px = candles[-1]["close"]
                hit = (
                    pos.side == Side.LONG and close_px >= tp
                ) or (
                    pos.side == Side.SHORT and close_px <= tp
                )
                if hit:
                    try:
                        self._market_close_open_position(
                            equity,
                            event="take_profit_hit",
                            message=f"Take profit (candle fallback) close={close_px:.4f} target={tp:.4f}",
                        )
                    except Exception as e:
                        self._system_log.insert(
                            level="ERROR",
                            event="take_profit_close_failed",
                            message=str(e),
                        )
                    return
            self._check_state_consistency()

    def _resolve_exit_price_from_trades(self, pos) -> float | None:
        """Derive exit price from recent userTrades after entry. Returns None if unavailable."""
        try:
            trades = self._execution.get_user_trades(self._symbol, limit=10)
            if not trades:
                return None
            try:
                entry_ts = int(
                    datetime.fromisoformat(pos.entry_time.replace("Z", "")).timestamp() * 1000
                )
            except (ValueError, TypeError, AttributeError):
                return None
            total_qty = 0.0
            total_value = 0.0
            for t in trades:
                trade_time = int(t.get("time", 0))
                if trade_time < entry_ts:
                    continue
                qty = float(t.get("qty", 0))
                price = float(t.get("price", 0))
                is_buyer = t.get("buyer")
                if pos.side == Side.LONG:
                    is_exit = not is_buyer
                else:
                    is_exit = bool(is_buyer)
                if qty > 0 and is_exit:
                    total_qty += qty
                    total_value += qty * price
            return total_value / total_qty if total_qty > 0 else None
        except Exception:
            return None

    def _is_position_flat_on_exchange(self) -> bool:
        """Check if exchange position amount is effectively zero."""
        try:
            live = self._execution.get_position(self._symbol)
            amt_raw = live.get("positionAmt") or live.get("position_amt") or 0
            return abs(float(amt_raw)) < 1e-8
        except Exception:
            return False

    def _market_close_open_position(
        self,
        equity: float,
        event: str,
        message: str,
    ) -> None:
        """Market-close OPEN position. Records trade via on_position_closed.

        Race-safe: if the market order response is incomplete but the exchange
        position is already flat, the close is confirmed via trade history
        instead of raising an error.
        """
        pos = self._state_machine.position
        if pos is None:
            return

        # ── Pre-flight: already flat on exchange? ──
        if self._is_position_flat_on_exchange():
            exit_price = self._resolve_exit_price_from_trades(pos)
            if exit_price is not None:
                self._system_log.insert(
                    level="INFO",
                    event="market_close_already_flat",
                    message=f"{message} Position already flat. Exit (from trades): {exit_price:.4f}",
                )
                self.on_position_closed(exit_price, equity)
                return
            logger.warning("[MARKET_CLOSE] Position flat on exchange but no exit price from trades")

        # ── Cancel protective orders ──
        if self._current_tp_algo_id:
            try:
                self._execution.cancel_order(self._symbol, self._current_tp_algo_id)
            except Exception:
                pass
            self._current_tp_algo_id = None
        if self._current_stop_order_id:
            try:
                self._execution.cancel_order(
                    self._symbol, self._current_stop_order_id
                )
            except Exception:
                pass
            self._current_stop_order_id = None

        # ── Place close market order ──
        close_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
        resp = self._execution.place_market_order(
            self._symbol,
            close_side,
            pos.size,
            _generate_client_order_id("bfat_market_close"),
        )
        resp = _validate_response_dict(resp)

        # ── Try standard fill parsing first ──
        try:
            _validate_market_response(resp)
            exit_price, _ = _parse_fill(resp)
            self._system_log.insert(
                level="INFO",
                event=event,
                message=f"{message} Exit: {exit_price:.4f}",
            )
            self.on_position_closed(exit_price, equity)
            return
        except RuntimeError as primary_err:
            logger.warning("[MARKET_CLOSE_RESPONSE_INCOMPLETE] %s — confirming via exchange", primary_err)

        # ── Fallback: confirm close via exchange position query ──
        time.sleep(0.3)
        if self._is_position_flat_on_exchange():
            exit_price = self._resolve_exit_price_from_trades(pos)
            if exit_price is None:
                current = self._execution.get_ticker_price(self._symbol)
                exit_price = current if current > 0 else pos.entry_price
                logger.warning("[MARKET_CLOSE_EXIT_PRICE_ESTIMATED] using ticker/entry as fallback: %.4f", exit_price)
            self._system_log.insert(
                level="INFO",
                event="market_close_confirmed_after_sync",
                message=f"{message} Confirmed flat after sync. Exit: {exit_price:.4f}",
            )
            self.on_position_closed(exit_price, equity)
            return

        # ── Genuine failure: position still open ──
        raise RuntimeError(
            f"Market close response incomplete and position still open: {primary_err}"
        )

    def on_position_closed(
        self,
        exit_price: float,
        equity: float,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
    ) -> None:
        """Handle position close: deterministic R at close only, kill switch, persist, state reset."""
        pos = self._state_machine.position
        if pos is None:
            return  # already closed (regime switch + user stream race)
        self._check_state_consistency()
        try:
            self._state_machine.on_exit_requested()
        except Exception as e:
            raise RuntimeError("State transition failed") from e

        de = lambda x: Decimal(str(x))
        entry = de(pos.entry_price)
        initial_stop = de(pos.initial_stop_price)
        exit_px = de(exit_price)
        size_d = de(pos.size)
        fee_entry = de(entry_fee)
        fee_exit = de(exit_fee)

        if pos.side == Side.LONG:
            gross_pnl_d = (exit_px - entry) * size_d
        else:
            gross_pnl_d = (entry - exit_px) * size_d
        net_pnl_d = gross_pnl_d - fee_entry - fee_exit

        initial_risk_d = abs(entry - initial_stop) * size_d
        r_multiple_d = (
            net_pnl_d / initial_risk_d if initial_risk_d > 0 else Decimal("0")
        )

        r_multiple = float(r_multiple_d)
        gross_pnl = float(gross_pnl_d)
        net_pnl = float(net_pnl_d)
        initial_risk = float(initial_risk_d)

        r_validation_status = "OK"
        if size_d <= 0:
            r_validation_status = "CRITICAL"
        elif initial_risk_d <= 0:
            r_validation_status = "CRITICAL"
        elif abs(r_multiple_d) > 100:
            r_validation_status = "CRITICAL_OUTLIER"
        elif abs(r_multiple_d) > 20:
            r_validation_status = "ANOMALY"
        elif net_pnl_d == 0 and r_multiple_d != 0:
            r_validation_status = "WARNING"
        else:
            expected_pnl = r_multiple_d * initial_risk_d
            delta = abs(expected_pnl - net_pnl_d)
            tolerance = max(Decimal("1e-8"), Decimal("0.0001") * initial_risk_d)
            if delta > tolerance:
                r_validation_status = "WARNING"
                print(
                    f"[R VALIDATION ERROR] entry={entry}, stop={initial_stop}, "
                    f"exit={exit_px}, size={size_d}, pnl={net_pnl_d}, "
                    f"expected={expected_pnl}, delta={delta}"
                )

        if r_validation_status in ("CRITICAL", "CRITICAL_OUTLIER"):
            self._system_log.insert(
                level="CRITICAL",
                event="r_validation_critical",
                message=f"R validation {r_validation_status}: entry={entry}, stop={initial_stop}, exit={exit_px}, size={size_d}, initial_risk={initial_risk}",
                payload={
                    "entry_price": float(entry),
                    "initial_stop": float(initial_stop),
                    "exit_price": float(exit_px),
                    "size": float(size_d),
                    "initial_risk": initial_risk,
                },
                correlation_id=pos.correlation_id,
            )

        _q = Decimal("0.0000000001")
        _entry_f = float(entry.quantize(_q))
        _stop_f = float(initial_stop.quantize(_q))
        _exit_f = float(exit_px.quantize(_q))
        _size_f = float(size_d.quantize(_q))
        _r_f = float(r_multiple_d.quantize(_q))
        _risk_f = float(initial_risk_d.quantize(_q))
        hash_input = (
            f"{_entry_f:.10f}|"
            f"{_stop_f:.10f}|"
            f"{_exit_f:.10f}|"
            f"{_size_f:.10f}|"
            f"{_r_f:.10f}|"
            f"{_risk_f:.10f}"
        )
        trade_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        print(f"[TRADE HASH INPUT] {hash_input}")

        print(
            f"[R CHECK] entry={entry}, stop={initial_stop}, "
            f"exit={exit_px}, size={size_d}, R={r_multiple}, status={r_validation_status}"
        )

        self._kill_switch.register_trade_result(r_multiple)
        self._kill_switch.update_equity(equity)
        self._trade_repo.insert(
            symbol=pos.symbol,
            side=pos.side.name,
            entry_time=pos.entry_time,
            entry_price=float(entry),
            size=float(size_d),
            exit_time=_ts(),
            exit_price=float(exit_px),
            pnl=gross_pnl,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            initial_risk=initial_risk,
            pnl_r=r_multiple,
            initial_stop_price=float(initial_stop),
            r_validation_status=r_validation_status,
            trade_hash=trade_hash,
            stop_phase=pos.stop_phase.value,
            signal_candle_ts=self._last_signal_candle_ts,
            correlation_id=pos.correlation_id,
        )
        self._equity_repo.insert(ts=_ts(), equity=equity)
        if self._notifier:
            self._notifier.notify_exit(pos, exit_price, gross_pnl, net_pnl, r_multiple)
        if self._current_tp_algo_id:
            try:
                self._execution.cancel_order(
                    self._symbol, self._current_tp_algo_id
                )
            except Exception:
                pass
        if self._current_stop_order_id:
            try:
                self._execution.cancel_order(
                    self._symbol, self._current_stop_order_id
                )
            except Exception:
                pass
        try:
            self._state_machine.on_exit_filled()
        except Exception as e:
            raise RuntimeError("State transition failed") from e
        self._current_stop_order_id = None
        self._sl_verified = False
        self._current_tp_algo_id = None
        self._current_take_profit = None
        self._tp_status = "none"
        self._tp_last_error = None
        self._entry_insight_snapshot = None
        self._post_close_cooldown_remaining = self.POST_CLOSE_COOLDOWN_CANDLES
        logger.info(
            "[POST_CLOSE_COOLDOWN] activated: %d candles before next entry",
            self._post_close_cooldown_remaining,
        )
        self._check_state_consistency()
