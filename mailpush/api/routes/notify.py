"""Notify + webhook routes — /api/v1/notify and /api/v1/webhooks."""
from fastapi import APIRouter, HTTPException

from ...api.schemas import NotifyRequest, WebhookEntry, WebhookRegistration
from ... import config as config_mgr
from ... import webhook
from ...delivery import dispatcher as delivery

router = APIRouter(tags=["notify"])


# ── Notify ────────────────────────────────────────────────────────────────────

@router.post("/notify")
async def send_notification(req: NotifyRequest):
    """Send a generic notification through all configured delivery adapters."""
    cfg = config_mgr.load()
    if not cfg.get("delivery_targets") and not cfg.get("deliveries"):
        raise HTTPException(400, "No delivery targets or adapters configured")
    results = await delivery.dispatch_notification(req.message, cfg)
    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "ok": True,
        "total": len(results),
        "successful": ok_count,
        "results": results,
    }


# ── Webhooks ──────────────────────────────────────────────────────────────────

@router.get("/webhooks")
async def list_webhooks():
    """List all registered webhooks."""
    return {"webhooks": webhook.list_all()}


@router.post("/webhooks", response_model=WebhookEntry)
async def register_webhook(req: WebhookRegistration):
    """Register a new webhook endpoint (HTTPS only)."""
    try:
        return webhook.register(req.url, req.secret)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/webhooks/{wid}")
async def remove_webhook(wid: str):
    """Unregister a webhook by ID."""
    if webhook.unregister(wid):
        return {"ok": True}
    raise HTTPException(404, f'Webhook "{wid}" not found')
