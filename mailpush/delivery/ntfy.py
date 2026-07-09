"""ntfy.sh delivery adapter."""
from __future__ import annotations

import logging
import time
from typing import List

import aiohttp

from .base import DeliveryAdapter

log = logging.getLogger("mailpush.delivery.ntfy")

NTFY_DEFAULT_SERVER = "https://ntfy.sh"


class NtfyAdapter(DeliveryAdapter):
    """Send push notifications via ntfy.sh (or a self-hosted ntfy server)."""

    type = "ntfy"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()

        server = self.config.get("server", NTFY_DEFAULT_SERVER).rstrip("/")
        topic = self.config.get("topic", "")
        if not topic:
            return self.result(False, message="Missing ntfy topic", started_at=started)

        token = self.config.get("token", "")
        timeout = int(self.config.get("timeout", 15))
        url = f"{server}/{topic}"

        # Build headers
        headers: dict = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Optional ntfy-specific headers
        priority = self.config.get("priority")       # e.g. "urgent", "high", "default", "low", "min"
        tags: List[str] = self.config.get("tags", [])
        click = self.config.get("click")             # URL to open on click
        title = self.config.get("title")

        if priority:
            headers["Priority"] = str(priority)
        if tags:
            headers["Tags"] = ",".join(tags) if isinstance(tags, list) else str(tags)
        if click:
            headers["Click"] = click
        if title:
            headers["Title"] = title

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=rendered_message.encode("utf-8"),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    body = await resp.text()
                    ok = 200 <= resp.status < 300
                    if not ok:
                        log.warning("ntfy push failed [%d]: %s", resp.status, body[:200])
                    return self.result(
                        ok,
                        status=resp.status,
                        message=body[:200] if not ok else "sent",
                        started_at=started,
                    )
        except Exception as exc:
            log.exception("ntfy send error")
            return self.result(False, message="ntfy send error", error=str(exc), started_at=started)
