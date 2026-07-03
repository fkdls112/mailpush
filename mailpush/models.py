"""Pydantic data models for MailPush API."""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ── Account ──────────────────────────────────────────

class EmailAccount(BaseModel):
    name: str = Field(..., description="Account identifier, e.g. 'QQ', 'Gmail'")
    host: str = Field(..., description="IMAP server hostname")
    port: int = Field(993, description="IMAP port")
    username: str = Field(..., description="IMAP username")
    password: str = Field(..., description="IMAP password / app-specific password")
    smtp_host: Optional[str] = Field(None, description="SMTP server for replies")
    smtp_port: Optional[int] = Field(587, description="SMTP port")
    smtp_username: Optional[str] = Field(None, description="SMTP username")
    smtp_password: Optional[str] = Field(None, description="SMTP password")


# ── Email ────────────────────────────────────────────

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


# ── Account Status ───────────────────────────────────

class AccountStatus(BaseModel):
    name: str
    connected: bool
    last_uid: int = 0
    last_event: Optional[datetime] = None
    emails_today: int = 0
    error: Optional[str] = None


# ── Webhook ──────────────────────────────────────────

class WebhookRegistration(BaseModel):
    url: str = Field(..., description="POST endpoint to receive email events")
    secret: Optional[str] = Field(None, description="HMAC secret for signature verification")


class WebhookEntry(BaseModel):
    id: str
    url: str
    created_at: str


# ── Config ───────────────────────────────────────────

class AppConfig(BaseModel):
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


# ── Reply ────────────────────────────────────────────

class ReplyRequest(BaseModel):
    account: str
    to: str
    subject: str
    body: str


# ── Notify ────────────────────────────────────────────

class NotifyRequest(BaseModel):
    """Generic notification — delivered to all configured delivery_targets."""
    message: str = Field(..., description="Notification body text")


# ── API Responses ────────────────────────────────────

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
