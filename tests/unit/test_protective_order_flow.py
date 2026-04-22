"""Unit tests for the fail-closed protective order flow.

Covers:
- SL/TP placement with verification
- Fail-closed on SL placement failure
- Fail-closed on TP placement failure
- SL re-registration sets _sl_verified correctly
- BinanceOrderError classification
"""

from unittest.mock import MagicMock, patch
import pytest

from app.core.execution.binance_client import (
    BinanceOrderError,
    _classify_error,
    _PERMANENT_ERROR_CODES,
    _RETRYABLE_ERROR_CODES,
)
from app.domain.enums import PositionState, Side, StopPhase
from app.domain.position import Position
from app.domain.signal import Signal
from app.domain.state_machine import StateMachine


# ────────────────────────────────────────────────────────────
# BinanceOrderError classification
# ────────────────────────────────────────────────────────────

class TestBinanceOrderErrorClassification:
    def test_permanent_error_not_retryable(self):
        for code in _PERMANENT_ERROR_CODES:
            err = _classify_error(code, "test")
            assert not err.retryable, f"code {code} should not be retryable"
            assert err.code == code

    def test_retryable_error_is_retryable(self):
        for code in _RETRYABLE_ERROR_CODES:
            err = _classify_error(code, "test")
            assert err.retryable, f"code {code} should be retryable"

    def test_unknown_error_defaults_retryable(self):
        err = _classify_error(-9999, "unknown")
        assert err.retryable


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _make_engine(
    *,
    sl_place_ok: bool = True,
    sl_verify_ok: bool = True,
    tp_place_ok: bool = True,
    tp_verify_ok: bool = True,
    market_order_resp: dict | None = None,
):
    """Build a BFATEngine with mocked dependencies for testing fail-closed logic."""
    from app.core.engine.engine import BFATEngine

    exec_client = MagicMock()
    exec_client.format_quantity.return_value = 0.01
    exec_client.format_price.side_effect = lambda _s, p, **kw: round(p, 2)
    exec_client._get_filters.return_value = {"price_step": 0.01, "qty_step": 0.001}
    exec_client.get_ticker_price.return_value = 100.0

    if market_order_resp is None:
        market_order_resp = {
            "status": "FILLED",
            "executedQty": "0.01",
            "avgPrice": "100.0",
            "orderId": "12345",
        }
    exec_client.place_market_order.return_value = market_order_resp

    if sl_place_ok:
        exec_client.place_stop_market_order.return_value = {"orderId": "SL_1", "algoId": "SL_1"}
    else:
        exec_client.place_stop_market_order.side_effect = RuntimeError("SL placement rejected")

    if tp_place_ok:
        exec_client.place_take_profit_market_order.return_value = {"orderId": "TP_1", "algoId": "TP_1"}
    else:
        exec_client.place_take_profit_market_order.side_effect = RuntimeError("TP placement rejected")

    exec_client.verify_algo_order_with_backoff.side_effect = (
        lambda _sym, oid: sl_verify_ok if "SL" in str(oid) else tp_verify_ok
    )
    exec_client.verify_algo_order_active.return_value = sl_verify_ok
    exec_client.cancel_order.return_value = {"orderId": "cancelled"}
    exec_client.cancel_all_algo_orders.return_value = 0

    sm = StateMachine()
    strategy = MagicMock()
    strategy.evaluate.return_value = None
    risk = MagicMock()
    risk.calculate_position_size.return_value = 0.01
    kill = MagicMock()
    kill.is_triggered.return_value = False
    trade_repo = MagicMock()
    trade_repo.query.return_value = []
    equity_repo = MagicMock()
    sys_log = MagicMock()

    engine = BFATEngine(
        strategy_engine=strategy,
        risk_manager=risk,
        kill_switch=kill,
        execution_client=exec_client,
        state_machine=sm,
        trade_repository=trade_repo,
        equity_repository=equity_repo,
        system_log_repository=sys_log,
        symbol="BTCUSDT",
    )
    return engine, exec_client, sm


def _signal(side=Side.LONG, stop_price=98.0, take_profit=105.0):
    return Signal(
        symbol="BTCUSDT",
        side=side,
        signal_time="2025-01-01T00:00:00Z",
        signal_candle_ts="ts1",
        stop_price=stop_price,
        take_profit=take_profit,
    )


# ────────────────────────────────────────────────────────────
# Entry fail-closed tests
# ────────────────────────────────────────────────────────────

class TestEntryFailClosed:
    """SL or TP failure on entry must flatten and leave engine FLAT."""

    def test_sl_failure_flattens_and_stays_flat(self):
        engine, exec_client, sm = _make_engine(sl_place_ok=False)
        sig = _signal()
        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20

        engine._strategy_engine.evaluate.return_value = sig
        with pytest.raises(RuntimeError, match="(?i)stop|flattened"):
            engine.on_candle_close(candles, equity=1000.0)

        assert sm.state == PositionState.FLAT
        assert engine._current_stop_order_id is None
        assert not engine._sl_verified

    def test_sl_verify_failure_flattens(self):
        engine, exec_client, sm = _make_engine(sl_place_ok=True, sl_verify_ok=False)
        sig = _signal()
        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20

        engine._strategy_engine.evaluate.return_value = sig
        with pytest.raises(RuntimeError, match="(?i)stop|flattened|not confirmed"):
            engine.on_candle_close(candles, equity=1000.0)

        assert sm.state == PositionState.FLAT

    def test_tp_failure_flattens_on_entry(self):
        engine, exec_client, sm = _make_engine(
            sl_place_ok=True, sl_verify_ok=True,
            tp_place_ok=False,
        )
        sig = _signal(take_profit=105.0)
        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20

        engine._strategy_engine.evaluate.return_value = sig
        with pytest.raises(RuntimeError, match="(?i)tp|flattened|fail"):
            engine.on_candle_close(candles, equity=1000.0)

        assert sm.state == PositionState.FLAT

    def test_successful_entry_sets_verified(self):
        engine, exec_client, sm = _make_engine()
        sig = _signal()
        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20

        engine._strategy_engine.evaluate.return_value = sig
        engine.on_candle_close(candles, equity=1000.0)

        assert sm.state == PositionState.OPEN
        assert engine._sl_verified is True
        assert engine._current_stop_order_id is not None
        assert engine._current_tp_algo_id is not None

    def test_entry_without_tp_succeeds(self):
        """When signal has no take_profit, entry should succeed without TP."""
        engine, exec_client, sm = _make_engine()
        sig = _signal(take_profit=None)
        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20

        engine._strategy_engine.evaluate.return_value = sig
        engine.on_candle_close(candles, equity=1000.0)

        assert sm.state == PositionState.OPEN
        assert engine._sl_verified is True
        assert engine._current_tp_algo_id is None
        assert engine._tp_status == "none"


# ────────────────────────────────────────────────────────────
# Runtime SL health check
# ────────────────────────────────────────────────────────────

class TestRuntimeSLHealthCheck:
    """SL re-registration must set _sl_verified correctly."""

    def _setup_open_position(self):
        engine, exec_client, sm = _make_engine()
        pos = Position(
            symbol="BTCUSDT", side=Side.LONG, size=0.01,
            entry_price=100.0, stop_price=98.0, initial_stop_price=98.0,
            stop_phase=StopPhase.INITIAL, entry_time="2025-01-01T00:00:00Z",
            correlation_id="test", take_profit=105.0,
        )
        sm.restore_position(pos)
        engine._current_stop_order_id = "OLD_SL"
        engine._sl_verified = True
        engine._current_tp_algo_id = "TP_1"
        engine._current_take_profit = 105.0
        engine._tp_status = "exchange"
        return engine, exec_client, sm

    def test_sl_reregistration_sets_verified(self):
        engine, exec_client, sm = self._setup_open_position()
        exec_client.verify_algo_order_active.return_value = False

        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20
        engine._strategy_engine.evaluate.return_value = None
        engine.on_candle_close(candles, equity=1000.0)

        assert engine._sl_verified is True
        assert engine._current_stop_order_id is not None

    def test_sl_active_confirms_verified(self):
        engine, exec_client, sm = self._setup_open_position()
        engine._sl_verified = False
        exec_client.verify_algo_order_active.return_value = True

        candles = [{"close": 100, "high": 101, "low": 99, "open": 100}] * 20
        engine._strategy_engine.evaluate.return_value = None
        engine.on_candle_close(candles, equity=1000.0)

        assert engine._sl_verified is True
