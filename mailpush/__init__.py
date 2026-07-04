# MailPush — real-time email push service
from mailpush.core.events import (
    MailEvent,
    DeliveryResult,
    EmailAccount,
    HealthResponse,
)

__all__ = ["MailEvent", "DeliveryResult", "EmailAccount", "HealthResponse"]
