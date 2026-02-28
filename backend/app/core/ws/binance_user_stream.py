"""User data stream for order/position updates."""

import asyncio
import json
from typing import Any, Callable

import websockets

from app.core.engine.engine import CancelFailureError, NewStopPlacementError
from app.core.ws._binance_rest import create_listen_key, keepalive_listen_key


BINANCE_WS_MAINNET = "wss://fstream.binance.com/ws"
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com/ws"
BINANCE_REST_MAINNET = "https://fapi.binance.com"
BINANCE_REST_TESTNET = "https://testnet.binancefuture.com"
KEEPALIVE_INTERVAL_SEC = 30 * 60


def _parse_order_trade_update(msg: dict) -> tuple[float, bool] | None:
    """If reduceOnly FILLED order, return (exit_price, True). Else None."""
    if msg.get("e") != "ORDER_TRADE_UPDATE":
        return None
    o = msg.get("o")
    if not isinstance(o, dict):
        return None
    if o.get("X") != "FILLED":
        return None
    if not o.get("R"):
        return None
    cid = str(o.get("c", ""))
    if not cid.startswith("bfat_"):
        return None
    try:
        ap = o.get("ap")
        if ap is not None and str(ap) != "":
            return float(ap), True
        L = o.get("L")
        if L is not None and str(L) != "":
            return float(L), True
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
            except Exception:
                pass

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
            except Exception:
                await asyncio.sleep(min(delay, 30))
                delay = min(delay * 2, 30.0)
                continue

            delay = 1.0
            url = f"{self._ws_base}/{listen_key}"
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            result = _parse_order_trade_update(msg)
                            if result:
                                exit_price, _ = result
                                try:
                                    equity = self._equity_provider()
                                except Exception:
                                    continue
                                if equity <= 0:
                                    continue
                                try:
                                    self._engine.on_position_closed(exit_price, equity)
                                except (CancelFailureError, NewStopPlacementError):
                                    continue
                                except RuntimeError:
                                    self._running = False
                                    raise
                        except json.JSONDecodeError:
                            continue
                        except websockets.ConnectionClosed:
                            break
                        except Exception:
                            break
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                raise
            except websockets.ConnectionClosed:
                pass
            except Exception:
                pass
            self._ws = None
            self._listen_key = None
            if self._running:
                delay = min(delay * 2, 30.0)
                await asyncio.sleep(delay)

    async def start(self) -> None:
        """Start user stream and keepalive."""
        self._running = True
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
