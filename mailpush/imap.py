"""Backward-compatibility shims for root-level mail modules.

These modules have moved to mailpush.mail.*  but are re-exported here
so any existing code importing from mailpush.imap / mailpush.filter etc.
continues to work without changes.
"""
from mailpush.mail.imap import (
    connect_all,
    get_status,
    OnEmailCallback,
)

__all__ = ["connect_all", "get_status", "OnEmailCallback"]
