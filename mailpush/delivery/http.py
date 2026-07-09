"""Generic HTTP delivery adapter."""
from __future__ import annotations

import time

import aiohttp

from .base import DeliveryAdapter
from mailpush.core.security import is_safe_url


class HttpAdapter(DeliveryAdapter):
    """Send MailEvent through a configurable HTTP request."""

    type = "http"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()
        url = self.config.get("url")
        if not url:
            return self.result(False, message="Missing HTTP url", started_at=started)
        if not is_safe_url(url):
            return self.result(False, message="SSRF blocked: unsafe URL", started_at=started)

        method = self.config.get("method", "POST").upper()
        timeout = int(self.config.get("timeout", 10))
        headers = dict(self.config.get("headers", {}))
        headers.setdefault("Content-Type", "application/json")
        payload = self.config.get("payload")
        if payload is None:
            payload = {
                "source": "mailpush",
                "event": getattr(event, "type", "email.received"),
                "text": rendered_message,
                "data": event.model_dump(mode="json") if hasattr(event, "model_dump") else event,
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method,
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    text = await resp.text()
                    return self.result(
                        200 <= resp.status < 300,
                        status=resp.status,
                        message=text[:200] or resp.reason,
                        started_at=started,
                    )
        except Exception as exc:
            return self.result(False, message="http delivery error", error=str(exc), started_at=started)
