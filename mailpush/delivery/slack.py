"""Slack Incoming Webhook delivery adapter."""
from __future__ import annotations

import logging
import time

import aiohttp

from .base import DeliveryAdapter

log = logging.getLogger("mailpush.delivery.slack")


class SlackAdapter(DeliveryAdapter):
    """Send messages via Slack Incoming Webhook."""

    type = "slack"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()

        webhook_url = self.config.get("webhook_url", "")
        if not webhook_url:
            return self.result(False, message="Missing Slack webhook_url", started_at=started)

        timeout = int(self.config.get("timeout", 15))

        payload: dict = {"text": rendered_message}

        # Optional overrides
        channel = self.config.get("channel")
        username = self.config.get("username")
        icon_emoji = self.config.get("icon_emoji")
        icon_url = self.config.get("icon_url")

        if channel:
            payload["channel"] = channel
        if username:
            payload["username"] = username
        if icon_emoji:
            payload["icon_emoji"] = icon_emoji
        if icon_url:
            payload["icon_url"] = icon_url

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    body = await resp.text()
                    ok = 200 <= resp.status < 300
                    if not ok:
                        log.warning("Slack webhook failed [%d]: %s", resp.status, body[:200])
                    return self.result(
                        ok,
                        status=resp.status,
                        message=body[:200] if not ok else "sent",
                        started_at=started,
                    )
        except Exception as exc:
            log.exception("Slack send error")
            return self.result(False, message="slack send error", error=str(exc), started_at=started)
