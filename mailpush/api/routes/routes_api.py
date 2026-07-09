"""Route-rule management — /api/v1/routes."""
import uuid

from fastapi import APIRouter, HTTPException

from ...api.schemas import RouteRuleCreate, RouteRuleOut, RouteRulePatch
from ... import config as config_mgr

router = APIRouter(tags=["routes"])


def _ensure_ids(cfg: dict) -> None:
    """Back-fill 'id' on any route entries that don't have one (config migration)."""
    for r in cfg.get("routes", []):
        if "id" not in r:
            r["id"] = uuid.uuid4().hex[:8]


@router.get("/routes")
async def list_routes():
    """List all routing rules."""
    cfg = config_mgr.load()
    _ensure_ids(cfg)
    return {"routes": cfg.get("routes", [])}


@router.post("/routes", response_model=RouteRuleOut, status_code=201)
async def add_route(rule: RouteRuleCreate):
    """Create a new routing rule."""
    cfg = config_mgr.load()
    _ensure_ids(cfg)
    new_id = uuid.uuid4().hex[:8]
    entry = {
        "id": new_id,
        "name": rule.name or f"rule-{new_id}",
        "match": rule.match.model_dump(exclude_none=True),
        "adapters": rule.adapters,
    }
    cfg.setdefault("routes", []).append(entry)
    config_mgr.save(cfg)
    return RouteRuleOut(
        id=new_id,
        name=entry["name"],
        match=rule.match,
        adapters=rule.adapters,
    )


@router.patch("/routes/{rule_id}", response_model=RouteRuleOut)
async def update_route(rule_id: str, patch: RouteRulePatch):
    """Partially update a routing rule."""
    cfg = config_mgr.load()
    _ensure_ids(cfg)
    entry = next((r for r in cfg.get("routes", []) if r["id"] == rule_id), None)
    if entry is None:
        raise HTTPException(404, f'Route rule "{rule_id}" not found')

    if patch.name is not None:
        entry["name"] = patch.name
    if patch.match is not None:
        entry["match"] = patch.match.model_dump(exclude_none=True)
    if patch.adapters is not None:
        entry["adapters"] = patch.adapters

    config_mgr.save(cfg)
    from ...api.schemas import RouteMatch
    return RouteRuleOut(
        id=entry["id"],
        name=entry.get("name", ""),
        match=RouteMatch(**entry.get("match", {})),
        adapters=entry.get("adapters", []),
    )


@router.delete("/routes/{rule_id}")
async def delete_route(rule_id: str):
    """Delete a routing rule."""
    cfg = config_mgr.load()
    _ensure_ids(cfg)
    before = len(cfg.get("routes", []))
    cfg["routes"] = [r for r in cfg.get("routes", []) if r["id"] != rule_id]
    if len(cfg["routes"]) == before:
        raise HTTPException(404, f'Route rule "{rule_id}" not found')
    config_mgr.save(cfg)
    return {"ok": True, "message": f'Route "{rule_id}" deleted'}
