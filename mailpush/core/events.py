"""Pydantic data models for MailPush.

Canonical location: mailpush.core.events
Legacy import path:  mailpush.models  (re-exported for back-compat)
"""
from pydantic import BaseModel, Field
from typing import Any, Dict, Literal, Optional, List
from datetime import datetime


# ── Account ───────────────────────────────────────────

class EmailAccount(BaseModel):
    name: str = Field(..., description="Account identifier, e.g. 'QQ', 'Gmail'")
    host: str = Field(..., description="IMAP server hostname")
    port: int = Field(993, description="IMAP port")
    username: str = Field(..., description="IMAP username")
    password: str = Field(..., description="IMAP password / app-specific password")
    enabled: bool = Field(True, description="Whether this account is active")
    type: str = Field("imap", description="Account protocol type")
    # Flat SMTP fields (legacy / convenience)
    smtp_host: Optional[str] = Field(None, description="SMTP server for replies")
    smtp_port: Optional[int] = Field(587, description="SMTP port")
    smtp_username: Optional[str] = Field(None, description="SMTP username")
    smtp_password: Optional[str] = Field(None, description="SMTP password")
    # Structured SMTP sub-object (preferred)
    smtp: Optional[Dict[str, Any]] = Field(None, description="SMTP config sub-object")


# ── Email ─────────────────────────────────────────────

class EmailSummary(BaseModel):
    ips: List[str] = Field(default_factory=list)
    amounts: List[str] = Field(default_factory=list)
    urls: List[str] = Field(default_factory=list)
    codes: List[str] = Field(default_factory=list)


class Attachment(BaseModel):
    name: str
    size: int  # bytes


class EmailNotification(BaseModel):
    account: str
    timestamp: str          # ISO 8601
    sender: str
    subject: str
    subject_cn: Optional[str] = None
    body_preview: str       # first 200 chars
    body_full: str          # complete body
    summary: EmailSummary = Field(default_factory=EmailSummary)
    attachments: List[Attachment] = Field(default_factory=list)


class MailEvent(BaseModel):
    """Canonical event passed to delivery adapters."""

    id: str
    type: Literal["email.received", "email.merged", "notify.custom"] = "email.received"
    account: str = ""
    timestamp: str
    sender: str = ""
    sender_email: Optional[str] = None
    recipients: List[str] = Field(default_factory=list)
    subject: str = ""
    subject_translated: Optional[str] = None
    body_preview: str = ""
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    summary: EmailSummary = Field(default_factory=EmailSummary)
    attachments: List[Attachment] = Field(default_factory=list)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    tags: List[str] = Field(default_factory=list)
    raw_headers: Dict[str, str] = Field(default_factory=dict)
    source: Dict[str, Any] = Field(default_factory=lambda: {
        "protocol": "imap",
        "uid": "",
        "message_id": "",
    })


class DeliveryResult(BaseModel):
    ok: bool
    adapter: str
    type: str
    status: int = 0
    message: str = ""
    latency_ms: int = 0
    error: Optional[str] = None


# ── Account Status ────────────────────────────────────

class AccountStatus(BaseModel):
    name: str
    connected: bool
    last_uid: int = 0
    last_event: Optional[datetime] = None
    emails_today: int = 0
    error: Optional[str] = None


# ── Webhook ───────────────────────────────────────────

class WebhookRegistration(BaseModel):
    url: str = Field(..., description="POST endpoint to receive email events")
    secret: Optional[str] = Field(None, description="HMAC secret for signature verification")


class WebhookEntry(BaseModel):
    id: str
    url: str
    created_at: str


# ── Config ────────────────────────────────────────────

class ProcessingConfig(BaseModel):
    """Processing pipeline configuration."""
    summary: bool = True
    translate: bool = False
    attachment_info: bool = True
    body_max_chars: int = Field(0, ge=0, description="0 = unlimited")
    merge_batch: bool = True
    merge_interval: int = Field(30, ge=5, le=300)


class AppConfig(BaseModel):
    """Legacy flat config model — still accepted by the API for backward compat."""
    translate: bool = False
    summary: bool = True
    attachment_info: bool = True
    merge_batch: bool = True
    merge_interval: int = Field(30, ge=5, le=300)
    filters: dict = Field(default_factory=lambda: {
        "block_senders": [],
        "block_keywords": [],
        "allow_only_senders": []
    })
    smtp_reply_from: str = ""


# ── Reply ─────────────────────────────────────────────

class ReplyRequest(BaseModel):
    account: str
    to: str
    subject: str
    body: str


# ── Notify ────────────────────────────────────────────

class NotifyRequest(BaseModel):
    """Generic notification — delivered to all configured delivery_targets."""
    message: str = Field(..., description="Notification body text")


# ── API Responses ─────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    uptime_seconds: float
    accounts_connected: int
    accounts_total: int


class ServerState(BaseModel):
    status: HealthResponse
    accounts: List[AccountStatus] = Field(default_factory=list)
    webhooks: List[WebhookEntry] = Field(default_factory=list)
    config: AppConfig = Field(default_factory=AppConfig)


class EmailsQuery(BaseModel):
    account: Optional[str] = None
    limit: int = Field(20, ge=1, le=100)
    since: Optional[str] = None  # ISO 8601 timestamp
