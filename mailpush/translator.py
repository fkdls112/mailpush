"""Backward-compatibility shim: mailpush.translator → mailpush.mail.translator"""
from mailpush.mail.translator import translate

__all__ = ["translate"]
