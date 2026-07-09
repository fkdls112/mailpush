"""Backward-compatibility shim: mailpush.smtp → mailpush.mail.smtp"""
from mailpush.mail.smtp import send_reply

__all__ = ["send_reply"]
