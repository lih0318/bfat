"""Telegram notification service for position entry/exit alerts."""

import asyncio
import logging
from typing import Optional

import httpx

from app.domain.position import Position

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends position alerts via Telegram Bot API.

    All public methods are safe to call even when disabled or misconfigured;
    failures are logged but never propagated to the caller.
    """

    _SEND_TIMEOUT = 10.0  # seconds

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = False) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled and bool(bot_token) and bool(chat_id)
        if self._enabled:
            self._base_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def _fire(self, text: str) -> None:
        """Schedule _send as a fire-and-forget task on the running event loop."""
        if not self._enabled:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send(text))
        except RuntimeError:
            logger.debug("No running event loop; skipping Telegram notification")

    async def _send(self, text: str) -> None:
        """POST message to Telegram. Swallows all exceptions."""
        try:
            async with httpx.AsyncClient(timeout=self._SEND_TIMEOUT) as client:
                resp = await client.post(
                    self._base_url,
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Telegram API returned %s: %s", resp.status_code, resp.text
                    )
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)

    def notify_entry(self, position: Position) -> None:
        """Send position entry alert."""
        side = position.side.value.upper()
        icon = "\U0001f7e2" if side == "LONG" else "\U0001f534"
        direction = "\U0001f4c8" if side == "LONG" else "\U0001f4c9"

        tp_line = ""
        if position.take_profit:
            tp_line = f"\n\U0001f3af TP       : {position.take_profit:,.2f} USDT"

        body = (
            f"\U0001f4b1 \uc2ec\ubcfc     : {position.symbol}\n"
            f"\U0001f4b0 \uc9c4\uc785\uac00   : {position.entry_price:,.2f} USDT\n"
            f"\U0001f4e6 \uc218\ub7c9     : {position.size}\n"
            f"\U0001f6d1 \uc190\uc808\uac00   : {position.stop_price:,.2f} USDT"
            f"{tp_line}"
        )

        text = (
            f"{icon} <b>{side} \ud3ec\uc9c0\uc158 \uc9c4\uc785</b> {direction}\n"
            f"\n"
            f"<pre>{body}</pre>"
        )
        self._fire(text)

    def notify_exit(
        self,
        position: Position,
        exit_price: float,
        gross_pnl: float,
        net_pnl: float,
        r_multiple: float,
    ) -> None:
        """Send position exit alert."""
        win = net_pnl >= 0
        icon = "\u2705" if win else "\u274c"
        mood = "\U0001f389" if win else "\U0001f4a8"
        pnl_sign = "+" if win else ""
        r_sign = "+" if r_multiple >= 0 else ""
        side = position.side.value.upper()

        pnl_pct = 0.0
        if position.entry_price > 0:
            if side == "LONG":
                pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
            else:
                pnl_pct = (position.entry_price - exit_price) / position.entry_price * 100
        pct_sign = "+" if pnl_pct >= 0 else ""

        body = (
            f"\U0001f4b1 \uc2ec\ubcfc     : {position.symbol} | {side}\n"
            f"\n"
            f"\u27a1\ufe0f \uc9c4\uc785\uac00   : {position.entry_price:,.2f}\n"
            f"\u2b05\ufe0f \uccad\uc0b0\uac00   : {exit_price:,.2f}\n"
            f"\n"
            f"\U0001f4b5 Gross    : {pnl_sign}{gross_pnl:,.2f} USDT\n"
            f"\U0001f4b2 Net      : {pnl_sign}{net_pnl:,.2f} USDT\n"
            f"\U0001f4ca \uc218\uc775\ub960   : {pct_sign}{pnl_pct:.2f}%\n"
            f"\U0001f3af R\ubc30\uc218    : {r_sign}{r_multiple:.2f}R"
        )

        text = (
            f"{icon} <b>\ud3ec\uc9c0\uc158 \uccad\uc0b0 \uc644\ub8cc</b> {mood}\n"
            f"\n"
            f"<pre>{body}</pre>"
        )
        self._fire(text)
