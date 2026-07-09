"""Backward-compatibility shim: mailpush.filter → mailpush.mail.filter"""
from mailpush.mail.filter import should_filter

__all__ = ["should_filter"]
