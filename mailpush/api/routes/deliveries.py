"""Delivery adapter routes — /api/v1/deliveries."""
from fastapi import APIRouter, HTTPException

from ...api.schemas import (
    DeliveryAdapterCreate,
    DeliveryAdapterOut,
    DeliveryAdapterTestResult,
)
from ... import config as config_mgr
from ...delivery import dispatcher as delivery

router = APIRouter(tags=["deliveries"])


@router.get("/deliveries")
async def list_deliveries():
    """List all configured delivery adapters (secrets redacted)."""
    cfg = config_mgr.load()
    return {"adapters": delivery.list_configured(cfg)}


@router.post("/deliveries", response_model=DeliveryAdapterOut, status_code=201)
async def add_delivery(adapter: DeliveryAdapterCreate):
    """Add a new delivery adapter to config."""
    cfg = config_mgr.load()
    existing = [d["name"] for d in cfg.get("deliveries", [])]
    if adapter.name in existing:
        raise HTTPException(409, f'Delivery adapter "{adapter.name}" already exists')
    cfg.setdefault("deliveries", []).append(
        {"name": adapter.name, "type": adapter.type, "config": adapter.config}
    )
    config_mgr.save(cfg)
    # Return safe version (redact secrets)
    safe_config = {
        k: "***" if k in ("token", "secret", "password") else v
        for k, v in adapter.config.items()
    }
    return DeliveryAdapterOut(name=adapter.name, type=adapter.type, config=safe_config)


@router.delete("/deliveries/{name}")
async def remove_delivery(name: str):
    """Remove a delivery adapter from config."""
    cfg = config_mgr.load()
    before = len(cfg.get("deliveries", []))
    cfg["deliveries"] = [d for d in cfg.get("deliveries", []) if d["name"] != name]
    if len(cfg["deliveries"]) == before:
        raise HTTPException(404, f'Delivery adapter "{name}" not found')
    config_mgr.save(cfg)
    return {"ok": True, "message": f'Adapter "{name}" removed'}


@router.post("/deliveries/{name}/test", response_model=DeliveryAdapterTestResult)
async def test_delivery_adapter(name: str):
    """Send a test message through a single named delivery adapter."""
    cfg = config_mgr.load()
    entry = next(
        (d for d in cfg.get("deliveries", []) if d["name"] == name), None
    )
    if entry is None:
        raise HTTPException(404, f'Delivery adapter "{name}" not found')

    # Build a minimal cfg with only this adapter so dispatch targets it alone
    test_cfg = dict(cfg)
    test_cfg["deliveries"] = [entry]
    test_cfg["delivery_targets"] = []  # suppress legacy adapters
    test_cfg["routes"] = []            # no routing — send directly

    results = await delivery.dispatch_notification(
        f"🧪 MailPush delivery test — adapter '{name}' check.",
        test_cfg,
    )
    ok_count = sum(1 for r in results if r.get("ok"))
    return DeliveryAdapterTestResult(
        ok=ok_count > 0,
        message=f"Test sent: {ok_count}/{len(results)} succeeded",
        results=results,
    )
