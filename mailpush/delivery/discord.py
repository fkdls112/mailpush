"""Discord Webhook delivery adapter."""
from __future__ import annotations

import logging
import time
from typing import List

import aiohttp

from .base import DeliveryAdapter

log = logging.getLogger("mailpush.delivery.discord")

DISCORD_MAX_LENGTH = 2000


def _split_message(text: str, limit: int = DISCORD_MAX_LENGTH) -> List[str]:
    """Split a message into chunks not exceeding `limit` characters."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class DiscordAdapter(DeliveryAdapter):
    """Send messages via Discord Incoming Webhook."""

    type = "discord"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()

        webhook_url = self.config.get("webhook_url", "")
        if not webhook_url:
            return self.result(False, message="Missing Discord webhook_url", started_at=started)

        username = self.config.get("username")
        avatar_url = self.config.get("avatar_url")
        timeout = int(self.config.get("timeout", 15))

        chunks = _split_message(rendered_message)
        last_result: dict = {}

        try:
            async with aiohttp.ClientSession() as session:
                for i, chunk in enumerate(chunks):
                    payload: dict = {"content": chunk}
                    if username:
                        payload["username"] = username
                    if avatar_url:
                        payload["avatar_url"] = avatar_url

                    async with session.post(
                        webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        # Discord returns 204 No Content on success
                        body = await resp.text()
                        ok = resp.status in (200, 204)
                        last_result = self.result(
                            ok,
                            status=resp.status,
                            message=body[:200] if not ok else f"sent ({i + 1}/{len(chunks)})",
                            started_at=started,
                        )
                        if not ok:
                            log.warning(
                                "Discord chunk %d/%d failed [%d]: %s",
                                i + 1, len(chunks), resp.status, body[:200],
                            )
                            return last_result
                        log.debug("Discord chunk %d/%d sent", i + 1, len(chunks))
        except Exception as exc:
            log.exception("Discord send error")
            return self.result(False, message="discord send error", error=str(exc), started_at=started)

        return last_result
