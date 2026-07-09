"""Webhook dispatcher — POSTs email events to registered URLs."""
import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from mailpush.core.events import EmailNotification, WebhookEntry

log = logging.getLogger('mailpush.webhook')

_webhooks: dict[str, WebhookEntry] = {}
"""Registered webhooks: {id: WebhookEntry}"""

_secrets: dict[str, Optional[str]] = {}


def register(url: str, secret: Optional[str] = None) -> WebhookEntry:
    """Register a new webhook URL. Enforces HTTPS. Returns the entry."""
    if not url.startswith('https://'):
        raise ValueError('Webhook URL must use HTTPS')
    wid = uuid.uuid4().hex[:12]
    entry = WebhookEntry(
        id=wid,
        url=url,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _webhooks[wid] = entry
    _secrets[wid] = secret
    log.info('Webhook registered: %s → %s', wid, url[:60])
    return entry


def unregister(wid: str) -> bool:
    """Remove a webhook. Returns True if found."""
    if wid in _webhooks:
        del _webhooks[wid]
        _secrets.pop(wid, None)
        return True
    return False


def list_all() -> list[WebhookEntry]:
    """Return all registered webhooks."""
    return list(_webhooks.values())


async def dispatch(notification: EmailNotification) -> list[dict]:
    """POST notification to all registered webhooks (HTTPS only, TLS verified)."""
    if not _webhooks:
        return []

    payload = notification.model_dump()
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    results = []

    # Enforce TLS verification
    import ssl
    tls_context = ssl.create_default_context()

    async with aiohttp.ClientSession() as session:
        for wid, entry in list(_webhooks.items()):
            try:
                headers = {'Content-Type': 'application/json; charset=utf-8'}
                secret = _secrets.get(wid)
                if secret:
                    from mailpush.core.security import sign_payload
                    sig = sign_payload(secret, body)
                    headers['X-Mailpush-Signature'] = sig

                async with session.post(
                    entry.url, data=body, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                    ssl=tls_context,
                ) as resp:
                    results.append({
                        'id': wid,
                        'url': entry.url,
                        'status': resp.status,
                        'ok': 200 <= resp.status < 300,
                    })
            except Exception as e:
                results.append({
                    'id': wid,
                    'url': entry.url,
                    'status': 0,
                    'ok': False,
                    'error': str(e),
                })

    return results
