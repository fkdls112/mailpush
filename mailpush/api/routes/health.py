"""Health routes — /api/v1/health and /api/health."""
import time

from fastapi import APIRouter

from ...api.schemas import HealthResponse
from ... import imap

router = APIRouter(tags=["health"])

# start time is owned by server.py; imported at runtime to avoid circular deps
_start_time: float = 0.0


def set_start_time(t: float) -> None:
    global _start_time
    _start_time = t


def _build_health() -> HealthResponse:
    statuses = imap.get_status()
    return HealthResponse(
        status="ok",
        version="1.0.0",
        uptime_seconds=time.time() - (_start_time or time.time()),
        accounts_connected=sum(1 for s in statuses.values() if s.connected),
        accounts_total=len(statuses),
    )


@router.get("/health", response_model=HealthResponse)
async def health():
    """System health check (no auth required)."""
    return _build_health()
