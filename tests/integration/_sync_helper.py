"""Helper to run _sync_binance_position logic in tests without full EngineService."""

import logging
from datetime import datetime, timezone

from app.core.execution import _generate_client_order_id
from app.domain.enums import Side, StopPhase
from app.domain.position import Position
from app.domain.state_machine import StateMachine

logger = logging.getLogger(__name__)


def run_sync(engine_mock, symbol: str, exec_client, sm: StateMachine) -> None:
    """Replicate the core sync logic from EngineService._sync_binance_position.

    This is a standalone function that tests the fail-closed restoration policy
    without needing the full EngineService scaffolding.
    """
    pos_data = exec_client.get_position(symbol)
    if not pos_data:
        return
    pos_amt = float(pos_data.get("positionAmt") or 0)
    if pos_amt == 0:
        return
    entry_price = float(pos_data.get("entryPrice") or 0)
    if entry_price <= 0:
        return
    side = Side.LONG if pos_amt > 0 else Side.SHORT
    size = abs(pos_amt)

    stop_order_id = None
    stop_price = entry_price
    tp_order_id = None
    take_profit_price = None

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

    sl_verified = False
    if stop_order_id is None:
        for margin in [0.015, 0.030, 0.050]:
            sp = round(
                entry_price * (1 - margin) if side == Side.LONG else entry_price * (1 + margin), 2,
            )
            try:
                resp = exec_client.place_stop_market_order(
                    symbol, side, size, sp, _generate_client_order_id("test_restore"),
                )
                new_id = str(resp.get("orderId", ""))
                if new_id and exec_client.verify_algo_order_with_backoff(symbol, new_id):
                    stop_order_id = new_id
                    stop_price = sp
                    sl_verified = True
                    break
            except Exception:
                continue
    else:
        try:
            sl_verified = exec_client.verify_algo_order_with_backoff(symbol, stop_order_id)
        except Exception:
            pass

    if not sl_verified:
        logger.critical("FAIL-CLOSED: SL not verified for %s, not restoring", symbol)
        return

    update_ts = pos_data.get("updateTime")
    entry_time = ""
    if update_ts:
        try:
            entry_time = (
                datetime.fromtimestamp(int(update_ts) / 1000, tz=timezone.utc)
                .isoformat(timespec="seconds") + "Z"
            )
        except (ValueError, TypeError, OSError):
            pass

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
    engine_mock._current_stop_order_id = stop_order_id
    engine_mock._sl_verified = True
    engine_mock._current_tp_algo_id = tp_order_id
    engine_mock._current_take_profit = take_profit_price
