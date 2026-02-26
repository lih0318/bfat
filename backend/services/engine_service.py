"""Engine service: runs BFAT engine + WebSocket streams in background."""

import asyncio
from typing import Any, Callable, Optional

from app.config.settings import Settings
from app.core.database import DatabaseFactory
from app.core.engine import BFATEngine
from app.core.execution import BinanceExecutionClient
from app.core.market.binance_market_stream import BinanceMarketStream
from app.core.risk import KillSwitch, RiskManager
from app.core.strategy.breakout import BreakoutStrategy
from app.core.ws._binance_rest import fetch_account_equity
from app.core.ws.binance_user_stream import BinanceUserStream
from app.domain.state_machine import StateMachine
from app.persistence import create_persistence


BINANCE_REST_MAINNET = "https://fapi.binance.com"
BINANCE_REST_TESTNET = "https://testnet.binancefuture.com"


class EngineService:
    """Wraps engine + streams. Runs in background. Exposes status."""

    def __init__(self, settings: Settings, db_factory: DatabaseFactory) -> None:
        self._settings = settings
        self._db = db_factory
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._engine: Optional[BFATEngine] = None
        self._market_stream: Optional[BinanceMarketStream] = None
        self._user_stream: Optional[BinanceUserStream] = None
        self._equity_cache: float = 0.0
        self._critical_error: Optional[str] = None

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
        strategy = BreakoutStrategy()
        state_machine = StateMachine()
        return BFATEngine(
            strategy=strategy,
            risk_manager=risk_manager,
            kill_switch=kill_switch,
            execution_client=execution,
            state_machine=state_machine,
            trade_repository=trade_repo,
            equity_repository=equity_repo,
            system_log_repository=system_log_repo,
            symbol=self._settings.bfat_symbol,
        )

    def _equity_provider(self) -> float:
        """Fetch equity from Binance REST."""
        rest_base = (
            BINANCE_REST_TESTNET
            if self._settings.binance_testnet
            else BINANCE_REST_MAINNET
        )
        eq = fetch_account_equity(
            self._settings.binance_api_key,
            self._settings.binance_api_secret,
            rest_base,
        )
        if eq > 0:
            self._equity_cache = eq
        return self._equity_cache if self._equity_cache > 0 else 0.0

    def _get_status(self) -> dict[str, Any]:
        """Build status dict for API/WebSocket."""
        if self._engine is None:
            return {
                "engine_state": "stopped",
                "position": None,
                "last_signal": None,
                "current_stop_price": None,
                "r_multiple": None,
                "r_validation_status": None,
                "system_health": "HEALTHY",
                "equity": self._equity_cache,
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
            "equity": self._equity_cache,
            "kill_switch_triggered": kill.is_triggered(),
            "error": self._critical_error,
        }

    async def _run(self) -> None:
        """Background: start streams and run until stopped."""
        self._critical_error = None
        self._engine = self._build_engine()
        try:
            eq = self._equity_provider()
            if eq > 0:
                self._equity_cache = eq
        except Exception:
            pass
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
        while self._running:
            await asyncio.sleep(1)
        await market.stop()
        await user.stop()
        self._market_stream = None
        self._user_stream = None

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
        """Return last strategy evaluation for insight API."""
        default = {
            "regime": "Unknown",
            "volatility_score": 0.0,
            "bb_width_percentile": 0.0,
            "atr_value": 0.0,
            "volume_ratio": 0.0,
            "engine_reasoning": ["No evaluation yet. Start the engine to receive insights."],
        }
        if self._engine is None:
            return default
        strategy = getattr(self._engine, "_strategy", None)
        if strategy is None:
            return default
        details = getattr(strategy, "get_last_evaluation_details", lambda: {})()
        if not details:
            return default
        return {
            "regime": details.get("regime", "Unknown"),
            "volatility_score": details.get("volatility_score", 0.0),
            "bb_width_percentile": details.get("bb_width_percentile", 0.0),
            "atr_value": details.get("atr_value", 0.0),
            "volume_ratio": details.get("volume_ratio", 0.0),
            "engine_reasoning": details.get("engine_reasoning", []),
        }
