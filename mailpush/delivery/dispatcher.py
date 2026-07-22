"""Delivery dispatcher — routes email events to configured adapters.

Responsibilities:
- Build adapter instances from config (new dict 'deliveries' + legacy list + legacy 'delivery_targets')
- Route events via 'routes' rules (accounts/keywords/min_priority matching)
- Render MailEvent → human-readable message for delivery
- Provide API-level entry points: dispatch_event, dispatch_message, dispatch_notification
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import DeliveryAdapter
from .command import CommandAdapter
from .hermes import HermesAdapter
from .http import HttpAdapter
from .openclaw import OpenClawAdapter
from .webhook import WebhookAdapter
import re

from mailpush.core.templates import render_event, auto_code_wrap  # noqa: F401 — re-exported

log = logging.getLogger("mailpush.dispatcher")

ADAPTER_CLASSES: dict[str, type[DeliveryAdapter]] = {
    "hermes": HermesAdapter,
    "http": HttpAdapter,
    "webhook": WebhookAdapter,
    "command": CommandAdapter,
    "openclaw": OpenClawAdapter,
}

_PRIORITY_ORDER = ["low", "normal", "high", "urgent"]


# ── Adapter construction ─────────────────────────────


def _build_adapters(config: dict) -> list[DeliveryAdapter]:
    """Build all adapters from config.

    Supports (in priority order):
    1. New dict format:  deliveries: {name: {type, config}}
    2. Old list format:  deliveries: [{name, type, config}]  (auto-migrated by core.config.load)
    3. Legacy:           delivery_targets: [str]  (auto-migrated by core.config.load)

    After core.config.load() runs, deliveries is always a dict, so paths 2 and 3
    are already normalised before this function is called.
    """
    adapters: list[DeliveryAdapter] = []

    raw = config.get("deliveries", {})

    # Defensive: handle if caller passes un-migrated list
    if isinstance(raw, list):
        from mailpush.core.config import _migrate_deliveries, _migrate_delivery_targets
        config = _migrate_deliveries(dict(config))
        config = _migrate_delivery_targets(config)
        raw = config.get("deliveries", {})

    for name, entry in raw.items():
        adapter_type = entry.get("type", "")
        cls = ADAPTER_CLASSES.get(adapter_type)
        if cls is None:
            log.warning("Unknown delivery type '%s' for adapter '%s' — skipping", adapter_type, name)
            continue
        try:
            # Strip "type" from entry config — everything else is adapter config
            adapter_cfg = {k: v for k, v in entry.items() if k != "type"}
            adapters.append(cls(name, adapter_cfg))
            log.info("Delivery adapter loaded: %s (%s)", name, adapter_type)
        except Exception as exc:
            log.error("Failed to create adapter '%s': %s", name, exc)

    return adapters


# ── Route matching ───────────────────────────────────


def _match_routes(
    event, routes: list[dict], all_adapters: list[DeliveryAdapter]
) -> list[DeliveryAdapter]:
    """Apply route rules to select which adapters receive an event.

    Route format (v2):
        {
          "match": {
            "accounts": ["QQ"],          # list of account names (alias: account)
            "keywords": ["invoice"],     # subject contains any keyword (alias: subject_contains)
            "sender_contains": "@x.com",
            "min_priority": "high",      # event priority >= this level
            "priority": ["urgent"],      # exact priority match (legacy)
            "tags": ["finance"],
          },
          "deliveries": ["adapter-name"],  # adapter names from deliveries dict (alias: adapters)
        }

    If no routes defined, all adapters receive all events.
    """
    if not routes:
        return all_adapters

    adapter_by_name = {a.name: a for a in all_adapters}
    matched_names: set[str] = set()

    for route in routes:
        condition = route.get("match", )
        if not _check_condition(event, condition):
            continue
        # Support both new 'deliveries' key and old 'adapters' key
        targets = route.get("deliveries") or route.get("adapters", [])
        for name in targets:
            matched_names.add(name)

    if not matched_names:
        log.debug("No routes matched event — delivering to all adapters")
        return all_adapters

    result = [adapter_by_name[n] for n in matched_names if n in adapter_by_name]
    log.debug("Routes matched %d adapters for event %s", len(result), getattr(event, "id", "?"))
    return result


def _check_condition(event, condition: dict) -> bool:
    """Check if an event matches a route condition."""
    # accounts / account (list or str)
    for key in ("accounts", "account"):
        if key in condition:
            allowed = condition[key]
            if isinstance(allowed, str):
                allowed = [allowed]
            if event.account not in allowed:
                return False
            break

    # sender_contains
    if "sender_contains" in condition:
        kw = condition["sender_contains"]
        if kw not in event.sender:
            return False

    # keywords / subject_contains
    for key in ("keywords", "subject_contains"):
        if key in condition:
            kw = condition[key]
            if isinstance(kw, str):
                kw = [kw]
            subject = getattr(event, "subject", "")
            if not any(k in subject for k in kw):
                return False
            break

    # min_priority — event priority must be >= this level
    if "min_priority" in condition:
        min_p = condition["min_priority"]
        event_priority = getattr(event, "priority", "normal")
        min_idx = _PRIORITY_ORDER.index(min_p) if min_p in _PRIORITY_ORDER else 0
        evt_idx = _PRIORITY_ORDER.index(event_priority) if event_priority in _PRIORITY_ORDER else 1
        if evt_idx < min_idx:
            return False

    # priority — exact match (legacy)
    if "priority" in condition:
        allowed = condition["priority"]
        if isinstance(allowed, str):
            allowed = [allowed]
        event_priority = getattr(event, "priority", "normal")
        if event_priority not in allowed:
            return False

    # tags
    if "tags" in condition:
        required = condition["tags"]
        if isinstance(required, str):
            required = [required]
        event_tags = getattr(event, "tags", [])
        if not any(t in event_tags for t in required):
            return False

    return True


# ── Public API ───────────────────────────────────────


async def dispatch_event(event, config: dict) -> list[dict]:
    """Full pipeline: render → route → deliver.

    Returns list of per-adapter result dicts.
    """
    rendered = render_event(event)
    rendered_code = auto_code_wrap(rendered)   # with <code> — for Telegram etc.
    rendered_plain = re.sub(r'</?code>', '', rendered_code)  # stripped — for WeChat
    adapters = _build_adapters(config)
    routes = config.get("routes", [])
    targets = _match_routes(event, routes, adapters)

    if not targets:
        log.warning("No delivery adapters configured for event %s", getattr(event, "id", "?"))
        return []

    log.info(
        "Dispatching event %s → %d adapter(s)",
        getattr(event, "id", "?"),
        len(targets),
    )
    results = []
    for adapter in targets:
        try:
            # WeChat / Hermes-weixin — skip <code> tags (WeChat doesn't render them)
            is_wechat = any(kw in adapter.name.lower() for kw in ("wechat", "weixin"))
            payload = rendered_plain if is_wechat else rendered_code
            result = await adapter.send(event, payload)
        except Exception as exc:
            log.error("Adapter '%s' crashed: %s", adapter.name, exc)
            result = {
                "ok": False,
                "adapter": adapter.name,
                "type": adapter.type,
                "error": str(exc),
            }
        results.append(result)
    return results


async def dispatch_message(message: str, config: dict) -> list[dict]:
    """Send a plain message through all configured adapters (no routing)."""
    rendered_code = auto_code_wrap(message)
    rendered_plain = re.sub(r'</?code>', '', rendered_code)
    adapters = _build_adapters(config)
    if not adapters:
        log.warning("No delivery adapters configured")
        return []

    results = []
    for adapter in adapters:
        try:
            is_wechat = any(kw in adapter.name.lower() for kw in ("wechat", "weixin"))
            payload = rendered_plain if is_wechat else rendered_code
            result = await adapter.send(None, payload)
        except Exception as exc:
            log.error("Adapter '%s' crashed: %s", adapter.name, exc)
            result = {
                "ok": False,
                "adapter": adapter.name,
                "type": adapter.type,
                "error": str(exc),
            }
        results.append(result)
    return results


# Alias
dispatch_notification = dispatch_message


async def test_config(name: str, config: dict, notification=None) -> dict:
    """Send a small test notification through one configured adapter."""
    adapters = _build_adapters(config)
    adapter = next((a for a in adapters if a.name == name), None)
    if adapter is None:
        return {
            "ok": False,
            "adapter": name,
            "type": "",
            "message": "delivery adapter not found",
            "error": f'Delivery "{name}" not found',
        }

    event = notification or {
        "type": "email.received",
        "account": "test",
        "timestamp": "",
        "sender": "test@example.com",
        "subject": "MailPush 测试通知",
        "body_preview": "这是一条来自 MailPush 管理面板的测试通知。",
        "summary": {},
        "attachments": [],
    }
    rendered = auto_code_wrap(render_event(event))
    try:
        is_wechat = any(kw in name.lower() for kw in ("wechat", "weixin"))
        payload = re.sub(r'</?code>', '', rendered) if is_wechat else rendered
        return await adapter.send(event, payload)
    except Exception as exc:
        return {
            "ok": False,
            "adapter": adapter.name,
            "type": adapter.type,
            "message": "delivery test crashed",
            "error": str(exc),
        }


def list_configured(config: dict) -> list[dict]:
    """Return a list of configured adapters (safe for API exposure)."""
    adapters = _build_adapters(config)
    result = []
    for a in adapters:
        safe_config = {}
        for k, v in a.config.items():
            safe_config[k] = "***" if k in ("token", "secret", "password") else v
        result.append({
            "name": a.name,
            "type": a.type,
            "config": safe_config,
        })
    return result
