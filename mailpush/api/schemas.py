"""Pydantic schemas for the MailPush API (v1).

All request/response models live here so route modules stay thin.
Models that already exist in mailpush.models are re-exported for convenience.
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Re-export from core models so callers can import from one place
from ..models import (  # noqa: F401
    AccountStatus,
    AppConfig,
    Attachment,
    DeliveryResult,
    EmailAccount,
    EmailNotification,
    EmailSummary,
    HealthResponse,
    MailEvent,
    NotifyRequest,
    ReplyRequest,
    ServerState,
    WebhookEntry,
    WebhookRegistration,
)


# ── Delivery adapter schemas ──────────────────────────────────────────────────

class DeliveryAdapterCreate(BaseModel):
    """Create / register a delivery adapter."""
    name: str = Field(..., description="Unique adapter identifier")
    type: Literal["hermes", "http", "webhook", "command", "openclaw"] = Field(
        ..., description="Adapter type"
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific configuration (secrets stored as-is)",
    )


class DeliveryAdapterOut(BaseModel):
    """Safe public representation of a delivery adapter (secrets redacted)."""
    name: str
    type: str
    config: Dict[str, Any]


class DeliveryAdapterTestResult(BaseModel):
    ok: bool
    message: str
    results: List[Dict[str, Any]] = Field(default_factory=list)


# ── Route-rule schemas ────────────────────────────────────────────────────────

class RouteMatch(BaseModel):
    account: Optional[List[str]] = None
    sender_contains: Optional[str] = None
    subject_contains: Optional[List[str]] = None
    priority: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class RouteRuleCreate(BaseModel):
    name: Optional[str] = Field(None, description="Human-readable label")
    match: RouteMatch = Field(default_factory=RouteMatch)
    adapters: List[str] = Field(..., description="Adapter names that receive matching events")


class RouteRuleOut(RouteRuleCreate):
    id: str


class RouteRulePatch(BaseModel):
    name: Optional[str] = None
    match: Optional[RouteMatch] = None
    adapters: Optional[List[str]] = None


# ── Redeliver schema ──────────────────────────────────────────────────────────

class RedeliverResult(BaseModel):
    ok: bool
    event_id: str
    total: int
    successful: int
    results: List[Dict[str, Any]] = Field(default_factory=list)


# ── Account test schema ───────────────────────────────────────────────────────

class AccountTestResult(BaseModel):
    ok: bool
    account: str
    message: str
    latency_ms: Optional[int] = None
    error: Optional[str] = None
