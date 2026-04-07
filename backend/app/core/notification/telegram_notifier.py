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
        tp_line = f"\nTP: {position.take_profit:,.2f} USDT" if position.take_profit else ""
        text = (
            f"<b>{position.side.value.upper()} 진입 완료</b>\n"
            f"심볼: {position.symbol}\n"
            f"진입가: {position.entry_price:,.2f} USDT\n"
            f"수량: {position.size}\n"
            f"손절가: {position.stop_price:,.2f} USDT"
            f"{tp_line}"
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
        pnl_sign = "+" if net_pnl >= 0 else ""
        r_sign = "+" if r_multiple >= 0 else ""
        text = (
            f"<b>포지션 청산 완료</b>\n"
            f"심볼: {position.symbol} | {position.side.value.upper()}\n"
            f"진입가: {position.entry_price:,.2f} → 청산가: {exit_price:,.2f}\n"
            f"Gross PnL: {pnl_sign}{gross_pnl:,.2f} USDT\n"
            f"Net PnL: {pnl_sign}{net_pnl:,.2f} USDT\n"
            f"R배수: {r_sign}{r_multiple:.2f}R"
        )
        self._fire(text)
