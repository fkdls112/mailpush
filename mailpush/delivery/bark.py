"""Bark push notification delivery adapter."""
from __future__ import annotations

import logging
import time

import aiohttp

from .base import DeliveryAdapter

log = logging.getLogger("mailpush.delivery.bark")

BARK_DEFAULT_SERVER = "https://api.day.app"


class BarkAdapter(DeliveryAdapter):
    """Send push notifications via Bark (iOS push service)."""

    type = "bark"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()

        server = self.config.get("server", BARK_DEFAULT_SERVER).rstrip("/")
        device_key = self.config.get("device_key", "")
        if not device_key:
            return self.result(False, message="Missing Bark device_key", started_at=started)

        timeout = int(self.config.get("timeout", 15))

        # Title: use configured title, or derive from first line of rendered_message
        title = self.config.get("title") or rendered_message.splitlines()[0][:64]
        body = rendered_message

        payload: dict = {
            "title": title,
            "body": body,
            "device_key": device_key,
        }

        # Optional Bark parameters
        sound = self.config.get("sound")           # e.g. "minuet"
        is_archive = self.config.get("isArchive")  # 1 = archive the notification
        level = self.config.get("level")           # "active" | "timeSensitive" | "passive"
        badge = self.config.get("badge")           # badge count (int)
        url = self.config.get("url")               # URL to open on tap
        group = self.config.get("group")           # notification group

        if sound:
            payload["sound"] = sound
        if is_archive is not None:
            payload["isArchive"] = is_archive
        if level:
            payload["level"] = level
        if badge is not None:
            payload["badge"] = badge
        if url:
            payload["url"] = url
        if group:
            payload["group"] = group

        push_url = f"{server}/push"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    push_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    body_text = await resp.text()
                    ok = 200 <= resp.status < 300
                    if not ok:
                        log.warning("Bark push failed [%d]: %s", resp.status, body_text[:200])
                    return self.result(
                        ok,
                        status=resp.status,
                        message=body_text[:200] if not ok else "sent",
                        started_at=started,
                    )
        except Exception as exc:
            log.exception("Bark send error")
            return self.result(False, message="bark send error", error=str(exc), started_at=started)
