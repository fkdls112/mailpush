"""Backward-compatibility shim: mailpush.models → mailpush.core.events

Old code doing `from mailpush.models import MailEvent` continues to work.
"""
from mailpush.core.events import (
    EmailAccount,
    EmailSummary,
    Attachment,
    EmailNotification,
    MailEvent,
    DeliveryResult,
    AccountStatus,
    WebhookRegistration,
    WebhookEntry,
    ProcessingConfig,
    AppConfig,
    ReplyRequest,
    NotifyRequest,
    HealthResponse,
    ServerState,
    EmailsQuery,
)

__all__ = [
    "EmailAccount",
    "EmailSummary",
    "Attachment",
    "EmailNotification",
    "MailEvent",
    "DeliveryResult",
    "AccountStatus",
    "WebhookRegistration",
    "WebhookEntry",
    "ProcessingConfig",
    "AppConfig",
    "ReplyRequest",
    "NotifyRequest",
    "HealthResponse",
    "ServerState",
    "EmailsQuery",
]
