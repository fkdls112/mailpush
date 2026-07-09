"""Backward-compatibility shim: mailpush.summarizer → mailpush.mail.summarizer"""
from mailpush.mail.summarizer import extract

__all__ = ["extract"]
