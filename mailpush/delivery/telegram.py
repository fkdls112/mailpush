"""Telegram Bot API delivery adapter."""
from __future__ import annotations

import logging
import time
from typing import List

import aiohttp

from .base import DeliveryAdapter

log = logging.getLogger("mailpush.delivery.telegram")

TELEGRAM_MAX_LENGTH = 4096


def _split_message(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> List[str]:
    """Split a message into chunks not exceeding `limit` characters."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


def _escape_html(text: str) -> str:
    """Escape HTML special characters to prevent injection when parse_mode='HTML'."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class TelegramAdapter(DeliveryAdapter):
    """Send messages via Telegram Bot API."""

    type = "telegram"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()

        bot_token = self.config.get("bot_token", "")
        chat_id = self.config.get("chat_id", "")
        if not bot_token:
            return self.result(False, message="Missing Telegram bot_token", started_at=started)
        if not chat_id:
            return self.result(False, message="Missing Telegram chat_id", started_at=started)

        # Default to plain text (no markup parsing) for safety; set parse_mode='HTML'
        # or parse_mode='MarkdownV2' in config only when explicitly needed.
        parse_mode = self.config.get("parse_mode", "")
        disable_preview = self.config.get("disable_web_page_preview", True)
        timeout = int(self.config.get("timeout", 15))
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        # Escape HTML entities in user-controlled content when HTML parse mode is active
        message_to_send = _escape_html(rendered_message) if parse_mode == "HTML" else rendered_message
        chunks = _split_message(message_to_send)
        last_result: dict = {}

        try:
            async with aiohttp.ClientSession() as session:
                for i, chunk in enumerate(chunks):
                    payload = {
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": disable_preview,
                    }
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        body = await resp.text()
                        ok = 200 <= resp.status < 300
                        last_result = self.result(
                            ok,
                            status=resp.status,
                            message=body[:200] if not ok else f"sent ({i + 1}/{len(chunks)})",
                            started_at=started,
                        )
                        if not ok:
                            log.warning(
                                "Telegram chunk %d/%d failed [%d]: %s",
                                i + 1, len(chunks), resp.status, body[:200],
                            )
                            return last_result
                        log.debug("Telegram chunk %d/%d sent", i + 1, len(chunks))
        except Exception as exc:
            log.exception("Telegram send error")
            return self.result(False, message="telegram send error", error=str(exc), started_at=started)

        return last_result
