"""Integration tests for engine startup protection recovery (fail-closed).

Tests _sync_binance_position behavior:
- Position with verified SL is restored
- Position without SL is NOT restored (fail-closed)
- Re-registration and verification on startup
"""

from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from app.domain.enums import PositionState, Side, StopPhase
from app.domain.position import Position
from app.domain.state_machine import StateMachine


def _mock_exec_client(
    *,
    has_position: bool = True,
    has_sl_algo: bool = True,
    sl_verify_ok: bool = True,
    safety_sl_ok: bool = True,
):
    """Mock execution client for _sync_binance_position tests."""
    client = MagicMock()
    client._base_url = "https://fapi.binance.com"

    if has_position:
        client.get_position.return_value = {
            "symbol": "BTCUSDT",
            "positionAmt": "0.01",
            "entryPrice": "100.0",
            "updateTime": "1700000000000",
        }
        client._request.return_value = [
            {"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.01"},
        ]
    else:
        client.get_position.return_value = {
            "symbol": "BTCUSDT",
            "positionAmt": "0",
            "entryPrice": "0",
        }
        client._request.return_value = []

    if has_sl_algo:
        client.get_open_algo_orders.return_value = [
            {
                "algoId": "999",
                "algoStatus": "NEW",
                "orderType": "STOP_MARKET",
                "triggerPrice": "98.0",
            },
        ]
    else:
        client.get_open_algo_orders.return_value = []

    client.get_open_orders.return_value = []

    def verify_backoff(sym, oid):
        return sl_verify_ok

    client.verify_algo_order_with_backoff.side_effect = verify_backoff
    client.verify_algo_order_active.return_value = sl_verify_ok

    if safety_sl_ok:
        client.place_stop_market_order.return_value = {"orderId": "SAFETY_SL", "algoId": "SAFETY_SL"}
    else:
        client.place_stop_market_order.side_effect = RuntimeError("SL rejected")

    client.format_price.side_effect = lambda sym, p, **kw: round(p, 2)
    client.format_quantity.return_value = 0.01

    return client


class TestSyncFailClosed:
    """_sync_binance_position must refuse to restore without verified SL."""

    def _build_service_mock(self, exec_client, sm):
        """Build a minimal mock of EngineService with the necessary attributes."""
        engine = MagicMock()
        engine._execution = exec_client
        engine._state_machine = sm
        engine._current_stop_order_id = None
        engine._sl_verified = False
        engine._current_tp_algo_id = None
        engine._current_take_profit = None
        return engine

    def test_position_with_verified_sl_is_restored(self):
        sm = StateMachine()
        client = _mock_exec_client(has_position=True, has_sl_algo=True, sl_verify_ok=True)
        engine = self._build_service_mock(client, sm)

        from tests.integration._sync_helper import run_sync
        run_sync(engine, "BTCUSDT", client, sm)

        assert sm.state == PositionState.OPEN
        assert engine._sl_verified is True

    def test_position_without_sl_not_restored(self):
        sm = StateMachine()
        client = _mock_exec_client(
            has_position=True, has_sl_algo=False,
            sl_verify_ok=False, safety_sl_ok=False,
        )
        engine = self._build_service_mock(client, sm)

        from tests.integration._sync_helper import run_sync
        run_sync(engine, "BTCUSDT", client, sm)

        assert sm.state == PositionState.FLAT, "Position must NOT be restored without verified SL"

    def test_no_position_stays_flat(self):
        sm = StateMachine()
        client = _mock_exec_client(has_position=False)
        engine = self._build_service_mock(client, sm)

        from tests.integration._sync_helper import run_sync
        run_sync(engine, "BTCUSDT", client, sm)

        assert sm.state == PositionState.FLAT
