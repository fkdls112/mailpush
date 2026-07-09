"""Email routes — /api/v1/emails and redeliver."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from ...api.schemas import EmailNotification, RedeliverResult
from ... import config as config_mgr
from ... import webhook
from ...delivery import dispatcher as delivery

router = APIRouter(tags=["emails"])

# Ring buffer shared with server; server calls set_store() on startup.
_store: List[EmailNotification] = []


def set_store(store: List[EmailNotification]) -> None:
    """Attach the server's shared email ring-buffer."""
    global _store
    _store = store


@router.get("/emails")
async def list_emails(
    account: Optional[str] = Query(None, description="Filter by account name"),
    limit: int = Query(20, ge=1, le=100),
    since: Optional[str] = Query(None, description="ISO 8601 timestamp lower bound"),
):
    """Return recent emails from the in-memory ring buffer."""
    results = list(_store)
    if account:
        results = [e for e in results if e.account == account]
    if since:
        results = [e for e in results if e.timestamp > since]
    page = results[:limit]
    return {
        "count": len(page),
        "total": len(results),
        "emails": page,
    }


@router.post("/events/{event_id}/redeliver", response_model=RedeliverResult)
async def redeliver_event(event_id: str):
    """Re-dispatch a previously received email through all configured delivery adapters.

    Looks up the event by its ``id`` field in the in-memory ring buffer.
    """
    # Find the matching event (EmailNotification doesn't have an id field;
    # we use array index encoded as "idx-N" or match on timestamp as fallback).
    event = None
    for i, e in enumerate(_store):
        if f"idx-{i}" == event_id or getattr(e, "id", None) == event_id:
            event = e
            break

    if event is None:
        raise HTTPException(404, f"Event '{event_id}' not found in recent buffer")

    cfg = config_mgr.load()
    if not cfg.get("delivery_targets") and not cfg.get("deliveries"):
        raise HTTPException(400, "No delivery adapters configured")

    results = await delivery.dispatch_event(event, cfg)
    ok_count = sum(1 for r in results if r.get("ok"))
    return RedeliverResult(
        ok=True,
        event_id=event_id,
        total=len(results),
        successful=ok_count,
        results=results,
    )
