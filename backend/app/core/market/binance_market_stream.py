"""15m kline WebSocket stream for BFAT."""

import asyncio
import json
from collections import deque
from typing import Any, Callable

import requests
import websockets

from app.core.engine.engine import CancelFailureError, NewStopPlacementError


BINANCE_WS_MAINNET = "wss://fstream.binance.com/ws"
BINANCE_REST_MAINNET = "https://fapi.binance.com"
BINANCE_REST_TESTNET = "https://testnet.binancefuture.com"
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com/ws"
MAX_CANDLE_BUFFER = 500
EQUITY_REFRESH_INTERVAL_SEC = 10


def _parse_closed_candle(msg: dict) -> dict | None:
    """Extract candle dict if kline is closed. Returns None if not closed."""
    if msg.get("e") != "kline":
        return None
    k = msg.get("k")
    if not isinstance(k, dict):
        return None
    if not k.get("x"):
        return None
    try:
        return {
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "timestamp": str(k["T"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


class BinanceMarketStream:
    """Subscribes to 15m kline stream, maintains candle buffer, calls engine on close."""

    def __init__(
        self,
        symbol: str,
        engine: Any,
        equity_provider: Callable[[], float],
        testnet: bool = False,
    ) -> None:
        self._symbol = symbol.lower()
        self._engine = engine
        self._equity_provider = equity_provider
        self._testnet = testnet
        self._ws_base = BINANCE_WS_TESTNET if testnet else BINANCE_WS_MAINNET
        self._rest_base = BINANCE_REST_TESTNET if testnet else BINANCE_REST_MAINNET
        self._candles: deque[dict] = deque(maxlen=MAX_CANDLE_BUFFER)
        self._running = False
        self._ws: Any = None
        self._equity_cache: float = 0.0
        self._equity_refresh_task: asyncio.Task | None = None
        self._run_task: asyncio.Task | None = None

    @property
    def candles(self) -> list[dict]:
        """Current candle buffer (copy)."""
        return list(self._candles)

    def _fetch_initial_klines(self, limit: int = 500) -> None:
        """Fetch initial closed candles via REST. Raises RuntimeError on failure."""
        url = f"{self._rest_base}/fapi/v1/klines"
        params = {"symbol": self._symbol.upper(), "interval": "15m", "limit": limit}
        resp = requests.get(url, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"Initial klines fetch failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError("Initial klines response not list")
        for bar in data:
            if len(bar) < 6:
                continue
            self._candles.append({
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": float(bar[5]),
                "timestamp": str(bar[6]),
            })

    async def _equity_refresh_loop(self) -> None:
        """Refresh equity every 10 seconds. Never reset to 0 on failure."""
        while self._running:
            await asyncio.sleep(EQUITY_REFRESH_INTERVAL_SEC)
            if not self._running:
                break
            try:
                val = self._equity_provider()
                if val > 0:
                    self._equity_cache = val
            except Exception:
                pass

    async def _run_loop(self) -> None:
        """Connect and process messages. Reconnect on disconnect."""
        url = f"{self._ws_base}/{self._symbol}@kline_15m"
        delay = 1.0
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    delay = 1.0
                    self._ws = ws
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            candle = _parse_closed_candle(msg)
                            if candle:
                                self._candles.append(candle)
                                candles_list = list(self._candles)
                                equity = self._equity_cache
                                if equity <= 0:
                                    try:
                                        self._equity_cache = self._equity_provider()
                                        equity = self._equity_cache
                                    except Exception:
                                        pass
                                if equity <= 0:
                                    try:
                                        self._engine.evaluate_for_insight(candles_list)
                                    except Exception:
                                        pass
                                    continue
                                try:
                                    self._engine.on_candle_close(candles_list, equity)
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
            if self._running:
                delay = min(delay * 2, 30.0)
                await asyncio.sleep(delay)

    async def start(self) -> None:
        """Start the stream. Fetches initial klines, then connects to WebSocket."""
        self._running = True
        self._fetch_initial_klines()
        try:
            val = self._equity_provider()
            if val > 0:
                self._equity_cache = val
        except Exception:
            pass
        self._equity_refresh_task = asyncio.create_task(self._equity_refresh_loop())
        self._run_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the stream. Cancel tasks, close ws."""
        self._running = False
        if self._equity_refresh_task:
            self._equity_refresh_task.cancel()
            try:
                await self._equity_refresh_task
            except asyncio.CancelledError:
                pass
            self._equity_refresh_task = None
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
        if self._ws:
            try:
                await self._ws.close()
            except websockets.ConnectionClosed:
                pass
            except Exception:
                pass
            self._ws = None
