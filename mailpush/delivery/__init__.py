"""Delivery adapter framework for MailPush."""
from .bark import BarkAdapter
from .discord import DiscordAdapter
from .dispatcher import (
    dispatch_event,
    dispatch_message,
    dispatch_notification,
    list_configured,
    render_event,
)
from .ntfy import NtfyAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter

__all__ = [
    # Adapters
    "BarkAdapter",
    "DiscordAdapter",
    "NtfyAdapter",
    "SlackAdapter",
    "TelegramAdapter",
    # Dispatcher API
    "dispatch_event",
    "dispatch_message",
    "dispatch_notification",
    "list_configured",
    "render_event",
]
