"""BFAT orchestrator. No business logic. Coordinates modules only."""

import hashlib
from datetime import datetime
from decimal import Decimal, getcontext
from typing import Any

from app.core.execution import BinanceExecutionClient, _generate_client_order_id
from app.domain.enums import PositionState, Side, StopPhase
from app.domain.position import Position


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
INITIAL_STOP_ATR = 1.2
TRAILING_STOP_ATR = 1.5


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


def _ts() -> str:
    """ISO timestamp."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


getcontext().prec = 28


class BFATEngine:
    """Orchestrates strategy, risk, execution, state machine, persistence."""

    def __init__(
        self,
        strategy: Any,
        risk_manager: Any,
        kill_switch: Any,
        execution_client: BinanceExecutionClient,
        state_machine: Any,
        trade_repository: Any,
        equity_repository: Any,
        system_log_repository: Any,
        symbol: str,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._kill_switch = kill_switch
        self._execution = execution_client
        self._state_machine = state_machine
        self._trade_repo = trade_repository
        self._equity_repo = equity_repository
        self._system_log = system_log_repository
        self._symbol = symbol
        self._current_stop_order_id: str | None = None
        self._last_signal_candle_ts: str = ""

    def _check_state_consistency(self) -> None:
        """Raise if engine state is inconsistent."""
        state = self._state_machine.state
        pos = self._state_machine.position
        if state == PositionState.OPEN:
            if pos is None:
                raise RuntimeError("Inconsistent state: OPEN without position")
            if self._current_stop_order_id is None:
                raise RuntimeError("Invariant: OPEN must have _current_stop_order_id")
        if state == PositionState.FLAT:
            if pos is not None:
                raise RuntimeError("Inconsistent state: FLAT with position")
            if self._current_stop_order_id is not None:
                raise RuntimeError("Invariant: FLAT must have _current_stop_order_id None")

    def evaluate_for_insight(self, candles: list[dict]) -> None:
        """Run strategy evaluation to populate Insight only. No orders, no position changes."""
        self._strategy.evaluate(candles)

    def on_candle_close(self, candles: list[dict], equity: float) -> None:
        """Handle candle close: evaluate signal, place orders, or trail stop."""
        self._check_state_consistency()
        self._kill_switch.update_equity(equity)
        if self._kill_switch.is_triggered():
            return
        if self._state_machine.state == PositionState.ENTERING:
            return

        if self._state_machine.state == PositionState.FLAT:
            signal = self._strategy.evaluate(candles)
            if not signal:
                return
            if signal.signal_candle_ts == self._last_signal_candle_ts:
                return
            atr_val = _atr(candles, ATR_PERIOD)
            if atr_val <= 0:
                return
            entry_price_est = candles[-1]["close"]
            stop_price_est = (
                entry_price_est - INITIAL_STOP_ATR * atr_val
                if signal.side == Side.LONG
                else entry_price_est + INITIAL_STOP_ATR * atr_val
            )
            position_size = self._risk_manager.calculate_position_size(
                equity, entry_price_est, stop_price_est
            )
            if position_size <= 0:
                return
            try:
                self._state_machine.on_signal(signal)
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
                stop_price = (
                    actual_entry_price - INITIAL_STOP_ATR * atr_val
                    if signal.side == Side.LONG
                    else actual_entry_price + INITIAL_STOP_ATR * atr_val
                )
                stop_id = _generate_client_order_id("bfat_stop")
                try:
                    stop_resp = self._execution.place_stop_market_order(
                        self._symbol,
                        signal.side,
                        actual_size,
                        stop_price,
                        stop_id,
                    )
                    self._current_stop_order_id = _validate_stop_response(stop_resp)
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
                )
                try:
                    self._state_machine.on_entry_filled(position)
                except Exception as e:
                    self._execution.cancel_order(
                        self._symbol, self._current_stop_order_id
                    )
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
                self._last_signal_candle_ts = signal.signal_candle_ts
                self._check_state_consistency()
            except RuntimeError:
                raise
            except Exception as e:
                if self._state_machine.state == PositionState.ENTERING:
                    self._state_machine.rollback_entry()
                raise RuntimeError("Entry failure") from e
            return

        if self._state_machine.state == PositionState.OPEN:
            pos = self._state_machine.position
            if pos is None:
                return
            if not self._current_stop_order_id:
                _close_position_flatten(
                    self._execution,
                    self._symbol,
                    pos.side,
                    pos.size,
                )
                raise RuntimeError("CRITICAL: stop order missing while OPEN")
            try:
                self._trailing_logic(candles, pos)
            except (CancelFailureError, NewStopPlacementError):
                raise
            except Exception as e:
                _close_position_flatten(
                    self._execution,
                    self._symbol,
                    pos.side,
                    pos.size,
                )
                raise RuntimeError("CRITICAL ENGINE FAILURE") from e
            self._check_state_consistency()

    def _trailing_logic(self, candles: list[dict], pos: Position) -> None:
        """Trailing stop logic. Assumes OPEN state and valid _current_stop_order_id."""
        atr_val = _atr(candles, ATR_PERIOD)
        if atr_val <= 0:
            return
        current_price = candles[-1]["close"]
        if pos.side == Side.LONG:
            new_stop = current_price - TRAILING_STOP_ATR * atr_val
        else:
            new_stop = current_price + TRAILING_STOP_ATR * atr_val
        if pos.side == Side.LONG and new_stop <= pos.stop_price:
            return
        if pos.side == Side.SHORT and new_stop >= pos.stop_price:
            return
        stop_id = _generate_client_order_id("bfat_stop")
        try:
            stop_resp = self._execution.place_stop_market_order(
                self._symbol,
                pos.side,
                pos.size,
                new_stop,
                stop_id,
            )
            new_stop_order_id = _validate_stop_response(stop_resp)
        except Exception as e:
            raise NewStopPlacementError(
                "New stop placement failed; old stop remains active"
            ) from e
        if self._current_stop_order_id:
            try:
                cancel_resp = self._execution.cancel_order(
                    self._symbol, self._current_stop_order_id
                )
                cancel_resp = _validate_response_dict(cancel_resp)
                _validate_cancel_response(cancel_resp)
            except Exception as e:
                raise CancelFailureError("Cancel failure; new stop active") from e
        self._current_stop_order_id = new_stop_order_id
        try:
            self._state_machine.on_stop_update(StopPhase.TRAILING, new_stop)
        except Exception as e:
            raise CancelFailureError(
                "State transition failed after trailing; new stop remains active"
            ) from e

    def on_position_closed(
        self,
        exit_price: float,
        equity: float,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
    ) -> None:
        """Handle position close: deterministic R at close only, kill switch, persist, state reset."""
        self._check_state_consistency()
        pos = self._state_machine.position
        if pos is None:
            raise ValueError("Cannot close: no active position")
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
        self._check_state_consistency()
