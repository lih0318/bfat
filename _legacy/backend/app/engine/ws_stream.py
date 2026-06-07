"""
WebSocket user data stream: listenKey management + event handlers.

Events handled:
  - ORDER_TRADE_UPDATE → fill detection → bracket trigger
  - ACCOUNT_UPDATE → equity updates
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Callable, Optional

from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)

# Keepalive interval (Binance requires every 30 min; we do every 20 min)
KEEPALIVE_INTERVAL_SEC = 20 * 60


class UserDataStream:
    """Manages a WebSocket user data stream for the engine."""

    def __init__(self) -> None:
        self._listen_key: str = ""
        self._ws: Any = None
        self._thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._running = False
        self._equity: float = 0.0
        self._on_fill: Optional[Callable[[dict[str, Any]], None]] = None
        self._on_account_update: Optional[Callable[[dict[str, Any]], None]] = None

    @property
    def equity(self) -> float:
        return self._equity

    def start(
        self,
        on_fill: Optional[Callable[[dict[str, Any]], None]] = None,
        on_account_update: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        """Start the user data stream."""
        if self._running:
            return
        self._on_fill = on_fill
        self._on_account_update = on_account_update
        self._running = True

        try:
            self._listen_key = binance_client.listen_key_create()
            if not self._listen_key:
                logger.error("ws_stream: failed to create listenKey")
                self._running = False
                return
        except Exception as exc:
            logger.error("ws_stream: listenKey create error: %s", exc)
            self._running = False
            return

        self._thread = threading.Thread(target=self._run_ws, daemon=True)
        self._thread.start()

        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

        logger.info("ws_stream: started with listenKey=%s...", self._listen_key[:8])

    def stop(self) -> None:
        """Stop the stream gracefully."""
        self._running = False
        try:
            if self._ws:
                # Signal close
                self._ws.close()
        except Exception:
            pass
        try:
            binance_client.listen_key_close()
        except Exception:
            pass
        self._listen_key = ""
        logger.info("ws_stream: stopped")

    def _run_ws(self) -> None:
        """WebSocket event loop (runs in a thread)."""
        import websockets.sync.client as ws_client

        ws_url = f"{binance_client.ws_base_url}/ws/{self._listen_key}"
        reconnect_delay = 1

        while self._running:
            try:
                with ws_client.connect(ws_url) as ws:
                    self._ws = ws
                    reconnect_delay = 1
                    logger.info("ws_stream: connected")

                    while self._running:
                        try:
                            raw = ws.recv(timeout=30)
                            if raw:
                                data = json.loads(raw)
                                self._handle_event(data)
                        except TimeoutError:
                            continue
                        except Exception as exc:
                            if self._running:
                                logger.warning("ws_stream: recv error: %s", exc)
                            break

            except Exception as exc:
                if self._running:
                    logger.warning("ws_stream: connection error: %s, reconnecting in %ds",
                                   exc, reconnect_delay)
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60)

        self._ws = None

    def _handle_event(self, data: dict[str, Any]) -> None:
        """Route WebSocket events."""
        event_type = data.get("e", "")

        if event_type == "ORDER_TRADE_UPDATE":
            self._handle_order_update(data)
        elif event_type == "ACCOUNT_UPDATE":
            self._handle_account_update(data)
        elif event_type == "listenKeyExpired":
            logger.warning("ws_stream: listenKey expired, reconnecting...")
            self._reconnect()

    def _handle_order_update(self, data: dict[str, Any]) -> None:
        """Process ORDER_TRADE_UPDATE: detect fills."""
        order = data.get("o", {})
        status = order.get("X", "")  # FILLED, PARTIALLY_FILLED, etc.
        symbol = order.get("s", "")
        side = order.get("S", "")
        filled_qty = float(order.get("z", 0) or 0)
        avg_price = float(order.get("ap", 0) or 0)
        realized_pnl = float(order.get("rp", 0) or 0)
        order_type = order.get("o", "")
        client_id = order.get("c", "")

        if status in ("FILLED", "PARTIALLY_FILLED") and filled_qty > 0:
            fill_event = {
                "symbol": symbol,
                "side": side,
                "filled_qty": filled_qty,
                "avg_price": avg_price,
                "realized_pnl": realized_pnl,
                "order_type": order_type,
                "client_id": client_id,
                "status": status,
            }
            logger.info("ws_stream: fill %s %s qty=%.6f @ %.2f pnl=%.4f",
                        symbol, side, filled_qty, avg_price, realized_pnl)
            if self._on_fill:
                try:
                    self._on_fill(fill_event)
                except Exception as exc:
                    logger.error("ws_stream: on_fill callback error: %s", exc)

    def _handle_account_update(self, data: dict[str, Any]) -> None:
        """Process ACCOUNT_UPDATE: update equity."""
        account_data = data.get("a", {})
        balances = account_data.get("B", [])
        for bal in balances:
            if bal.get("a") == "USDT":
                self._equity = float(bal.get("wb", 0) or 0)
                break

        if self._on_account_update:
            try:
                self._on_account_update(account_data)
            except Exception as exc:
                logger.error("ws_stream: on_account_update error: %s", exc)

    def _keepalive_loop(self) -> None:
        """Send keepalive every 20 minutes."""
        while self._running:
            time.sleep(KEEPALIVE_INTERVAL_SEC)
            if not self._running:
                break
            try:
                binance_client.listen_key_keepalive()
                logger.debug("ws_stream: keepalive sent")
            except Exception as exc:
                logger.warning("ws_stream: keepalive failed: %s", exc)
                self._reconnect()

    def _reconnect(self) -> None:
        """Reconnect by refreshing the listenKey."""
        try:
            self._listen_key = binance_client.listen_key_create()
            logger.info("ws_stream: reconnected with new listenKey")
        except Exception as exc:
            logger.error("ws_stream: reconnect failed: %s", exc)
