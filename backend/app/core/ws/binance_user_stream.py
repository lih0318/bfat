"""User data stream for order/position updates."""

import asyncio
import json
import logging
import time
from typing import Any, Callable

import websockets

from app.core.engine.engine import CancelFailureError, NewStopPlacementError
from app.core.ws._binance_rest import create_listen_key, keepalive_listen_key

logger = logging.getLogger(__name__)

BINANCE_WS_MAINNET = "wss://fstream.binance.com/ws"
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com/ws"
BINANCE_REST_MAINNET = "https://fapi.binance.com"
BINANCE_REST_TESTNET = "https://testnet.binancefuture.com"
KEEPALIVE_INTERVAL_SEC = 30 * 60


def _parse_order_trade_update(msg: dict, expected_symbol: str | None = None) -> tuple[float, str] | None:
    """If reduceOnly FILLED order, return (exit_price, symbol). Else None."""
    if msg.get("e") != "ORDER_TRADE_UPDATE":
        return None
    o = msg.get("o")
    if not isinstance(o, dict):
        return None
    if o.get("X") != "FILLED":
        return None
    reduce_only = bool(o.get("R") or o.get("r"))
    close_all = bool(o.get("cp"))
    if not (reduce_only or close_all):
        return None
    cid = str(o.get("c", ""))
    symbol_in_msg = str(o.get("s", ""))
    is_engine_order = cid.startswith("bfat_")
    if not is_engine_order:
        if expected_symbol is None or symbol_in_msg != expected_symbol:
            return None
    try:
        ap = o.get("ap")
        if ap is not None and str(ap) != "":
            return float(ap), symbol_in_msg
        L = o.get("L")
        if L is not None and str(L) != "":
            return float(L), symbol_in_msg
        return None
    except (TypeError, ValueError):
        return None


class BinanceUserStream:
    """User data stream: listen key, ORDER_TRADE_UPDATE, keepalive."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        engine: Any,
        equity_provider: Callable[[], float],
        testnet: bool = False,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._engine = engine
        self._equity_provider = equity_provider
        self._rest_base = BINANCE_REST_TESTNET if testnet else BINANCE_REST_MAINNET
        self._ws_base = BINANCE_WS_TESTNET if testnet else BINANCE_WS_MAINNET
        self._running = False
        self._ws: Any = None
        self._listen_key: str | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._run_task: asyncio.Task | None = None
        # ── Observability fields (read-only externally) ──
        self._connected: bool = False
        self._last_message_ts: float = 0.0
        self._last_disconnect_ts: float = 0.0
        self._last_error: str = ""
        self._reconnect_count: int = 0
        self._current_backoff: float = 0.0

    def get_diagnostics(self) -> dict[str, Any]:
        """Return stream health snapshot for status API."""
        return {
            "connected": self._connected,
            "last_message_ts": self._last_message_ts,
            "last_disconnect_ts": self._last_disconnect_ts,
            "last_error": self._last_error,
            "reconnect_count": self._reconnect_count,
            "current_backoff": self._current_backoff,
        }

    async def _keepalive_loop(self) -> None:
        """PUT listenKey every 30 minutes. Uses current _listen_key session."""
        while self._running:
            await asyncio.sleep(KEEPALIVE_INTERVAL_SEC)
            if not self._running:
                break
            key = self._listen_key
            if not key:
                continue
            try:
                keepalive_listen_key(
                    self._api_key,
                    self._api_secret,
                    self._rest_base,
                    key,
                )
            except Exception as e:
                logger.warning("USER_WS_KEEPALIVE_FAILED %s: %s", type(e).__name__, e)

    async def _run_loop(self) -> None:
        """Connect and process. Reconnect on disconnect."""
        delay = 1.0
        while self._running:
            try:
                listen_key = create_listen_key(
                    self._api_key,
                    self._api_secret,
                    self._rest_base,
                )
                self._listen_key = listen_key
            except Exception as e:
                self._last_error = f"listen_key: {type(e).__name__}: {e}"
                logger.warning("USER_WS_LISTEN_KEY_ERROR %s: %s", type(e).__name__, e)
                await asyncio.sleep(min(delay, 30))
                delay = min(delay * 2, 30.0)
                self._current_backoff = delay
                continue

            delay = 1.0
            self._current_backoff = 0.0
            url = f"{self._ws_base}/{listen_key}"
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    logger.info("USER_WS_OPENED")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._last_message_ts = time.time()
                        try:
                            msg = json.loads(raw)
                            expected_symbol = getattr(self._engine, "_symbol", None)
                            result = _parse_order_trade_update(msg, expected_symbol=expected_symbol)
                            if result:
                                exit_price, symbol_in_msg = result
                                logger.info(
                                    "[REDUCE_ONLY_FILLED]",
                                    extra={"symbol": symbol_in_msg, "price": exit_price},
                                )
                                try:
                                    equity = self._equity_provider()
                                except Exception:
                                    equity = 0.0
                                try:
                                    self._engine.on_reduce_only_fill_from_stream(
                                        exit_price, equity,
                                    )
                                except (CancelFailureError, NewStopPlacementError):
                                    continue
                                except RuntimeError:
                                    self._running = False
                                    raise
                        except json.JSONDecodeError:
                            continue
                        except websockets.ConnectionClosed as e:
                            logger.warning("USER_WS_CLOSED code=%s reason=%s", e.code, e.reason)
                            break
                        except Exception as e:
                            logger.warning("USER_WS_MSG_ERROR %s: %s", type(e).__name__, e)
                            self._last_error = f"{type(e).__name__}: {e}"
                            break
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                raise
            except websockets.ConnectionClosed as e:
                logger.warning("USER_WS_CLOSED (outer) code=%s reason=%s", e.code, e.reason)
            except Exception as e:
                logger.warning("USER_WS_ERROR %s: %s", type(e).__name__, e, exc_info=True)
                self._last_error = f"{type(e).__name__}: {e}"
            self._ws = None
            self._connected = False
            self._last_disconnect_ts = time.time()
            self._listen_key = None
            if self._running:
                self._reconnect_count += 1
                delay = min(delay * 2, 30.0)
                self._current_backoff = delay
                logger.info(
                    "USER_WS_RECONNECT_SCHEDULED delay=%.1fs reconnect_count=%d",
                    delay, self._reconnect_count,
                )
                await asyncio.sleep(delay)

    async def start(self) -> None:
        """Start user stream and keepalive."""
        self._running = True
        self._reconnect_count = 0
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        self._run_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the stream. Cancel tasks, close ws."""
        self._running = False
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
        self._listen_key = None
        if self._ws:
            try:
                await self._ws.close()
            except websockets.ConnectionClosed:
                pass
            except Exception:
                pass
            self._ws = None
