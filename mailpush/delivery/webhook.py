"""Configured webhook delivery adapter."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import ssl
import time
from datetime import datetime, timezone

import aiohttp

from .base import DeliveryAdapter
from mailpush.core.security import is_safe_url


class WebhookAdapter(DeliveryAdapter):
    """POST the full MailEvent to an HTTPS webhook."""

    type = "webhook"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()
        url = self.config.get("url")
        secret = self.config.get("secret", "")
        timeout = int(self.config.get("timeout", 10))
        allow_insecure = bool(self.config.get("allow_insecure", False))
        if not url:
            return self.result(False, message="Missing webhook url", started_at=started)
        if not url.startswith("https://") and not allow_insecure:
            return self.result(False, message="Webhook url must use HTTPS", started_at=started)
        if not is_safe_url(url):
            return self.result(False, message="SSRF blocked: unsafe URL", started_at=started)

        payload = {
            "source": "mailpush",
            "event": getattr(event, "type", "email.received"),
            "text": rendered_message,
            "data": event.model_dump(mode="json") if hasattr(event, "model_dump") else event,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce = secrets.token_hex(16)
        headers["X-Mailpush-Timestamp"] = timestamp
        headers["X-Mailpush-Nonce"] = nonce
        if secret:
            sign_input = (timestamp + "." + nonce + ".").encode("utf-8") + body
            digest = hmac.new(secret.encode("utf-8"), sign_input, hashlib.sha256).hexdigest()
            headers["X-Mailpush-Signature"] = digest
            headers["X-Mailpush-Signature-256"] = f"sha256={digest}"

        ssl_context = None if allow_insecure else ssl.create_default_context()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    ssl=ssl_context,
                ) as resp:
                    text = await resp.text()
                    return self.result(
                        200 <= resp.status < 300,
                        status=resp.status,
                        message=text[:200] or resp.reason,
                        started_at=started,
                    )
        except Exception as exc:
            return self.result(False, message="webhook error", error=str(exc), started_at=started)
