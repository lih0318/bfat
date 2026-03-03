"""Engine service: runs BFAT engine + WebSocket streams in background."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

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
        )

    _EQUITY_TTL = 5.0  # seconds — fetch from Binance at most once per TTL

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

    def _fetch_binance_position(self) -> dict[str, Any] | None:
        """Query Binance for a live position on the configured symbol. Returns pos dict or None."""
        if not self._binance_account.is_configured():
            return None
        try:
            acct = self._binance_account.account()
            for p in acct.get("positions", []):
                if p.get("symbol") == self._settings.bfat_symbol:
                    amt = float(p.get("positionAmt", 0))
                    if amt == 0:
                        return None
                    entry_price = float(p.get("entryPrice", 0))
                    unrealized = float(p.get("unrealizedProfit", 0))
                    return {
                        "symbol": self._settings.bfat_symbol,
                        "side": "long" if amt > 0 else "short",
                        "size": abs(amt),
                        "entry_price": entry_price,
                        "stop_price": 0,
                        "initial_stop_price": 0,
                        "stop_phase": "unknown",
                        "entry_time": "",
                        "correlation_id": "binance_live",
                        "unrealized_pnl": unrealized,
                        "source": "binance",
                    }
            return None
        except Exception as e:
            logger.debug("Binance live position fetch failed: %s", e)
            return None

    def _get_status(self) -> dict[str, Any]:
        """Build status dict for API/WebSocket."""
        equity = self._equity_provider()
        if self._engine is None:
            live_pos = self._fetch_binance_position()
            return {
                "engine_state": "stopped",
                "position": live_pos,
                "last_signal": None,
                "current_stop_price": None,
                "r_multiple": None,
                "r_validation_status": None,
                "system_health": "HEALTHY",
                "equity": equity,
                "kill_switch_triggered": False,
                "error": self._critical_error,
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
        if r_validation_status in ("CRITICAL", "CRITICAL_OUTLIER"):
            system_health = "DEGRADED"
        return {
            "engine_state": sm.state.value,
            "position": pos_dict,
            "last_signal": last_signal,
            "current_stop_price": pos.stop_price if pos else None,
            "r_multiple": r_multiple,
            "r_validation_status": r_validation_status,
            "system_health": system_health,
            "equity": equity,
            "kill_switch_triggered": kill.is_triggered(),
            "error": self._critical_error,
        }

    def _sync_binance_position(self) -> None:
        """On engine start, check Binance for an existing open position and restore into StateMachine."""
        if self._engine is None:
            return
        symbol = self._settings.bfat_symbol
        exec_client = self._engine._execution
        sm = self._engine._state_machine
        try:
            pos_data = exec_client.get_position(symbol)
            if not pos_data:
                return
            pos_amt = float(pos_data.get("positionAmt", 0))
            if pos_amt == 0:
                return
            entry_price = float(pos_data.get("entryPrice", 0))
            if entry_price <= 0:
                return
            side = Side.LONG if pos_amt > 0 else Side.SHORT
            size = abs(pos_amt)

            stop_order_id: str | None = None
            stop_price = entry_price
            try:
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

            if stop_order_id is None:
                safety_pct = 0.015
                if side == Side.LONG:
                    stop_price = round(entry_price * (1 - safety_pct), 2)
                else:
                    stop_price = round(entry_price * (1 + safety_pct), 2)
                try:
                    stop_resp = exec_client.place_stop_market_order(
                        symbol, side, size, stop_price,
                        _generate_client_order_id("bfat_restore_stop"),
                    )
                    stop_order_id = str(stop_resp.get("orderId", ""))
                    logger.info("Placed safety stop for restored position: %s @ %.2f", stop_order_id, stop_price)
                except Exception as e:
                    logger.warning(
                        "Cannot fully restore position: stop order placement failed (%s). "
                        "Position will still appear via Binance fallback.",
                        e,
                    )
                    return

            update_ts = pos_data.get("updateTime")
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
            )
            sm.restore_position(position)
            self._engine._current_stop_order_id = stop_order_id
            logger.info(
                "Restored Binance position: %s %s %.6f @ %.2f, stop=%.2f (order=%s)",
                side.value, symbol, size, entry_price, stop_price, stop_order_id,
            )
        except Exception as e:
            logger.warning("Binance position sync failed: %s", e)

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

    def get_trades(self, symbol: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Return closed trades with stored r_multiple from DB only."""
        trade_repo = None
        if self._engine is not None:
            trade_repo = getattr(self._engine, "_trade_repo", None)
        if trade_repo is None:
            trade_repo, _, _ = create_persistence(self._db)
        rows = trade_repo.query(symbol=symbol, limit=limit, offset=offset)
        return [
            {
                "id": r.get("id"),
                "symbol": r.get("symbol"),
                "side": r.get("side"),
                "entry_time": r.get("entry_time"),
                "entry_price": r.get("entry_price"),
                "exit_time": r.get("exit_time"),
                "exit_price": r.get("exit_price"),
                "size": r.get("size"),
                "initial_stop_price": r.get("initial_stop_price"),
                "pnl": r.get("pnl"),
                "r_multiple": r.get("pnl_r"),
                "r_validation_status": r.get("r_validation_status"),
            }
            for r in rows
        ]

    def get_insight(self) -> dict[str, Any]:
        """Return last strategy evaluation + regime classifier data for insight API."""
        default: dict[str, Any] = {
            "regime": "Unknown",
            "active_strategy": "Unknown",
            "regime_changed": False,
            "volatility_score": 0.0,
            "bb_width_percentile": 0.0,
            "atr_value": 0.0,
            "volume_ratio": 0.0,
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
            "bb_width_percentile": details.get("bb_width_percentile", 0.0),
            "atr_value": details.get("atr_value", 0.0),
            "volume_ratio": details.get("volume_ratio", 0.0),
        }
        if "bb_width_z" in details:
            result["bb_width_z"] = details["bb_width_z"]
        if "compression_model" in details:
            result["compression_model"] = details["compression_model"]
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
        return result
