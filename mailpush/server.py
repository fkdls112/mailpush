"""Backward-compatibility shim: mailpush.server → mailpush.api.server

uvicorn is still pointed at 'mailpush.server:app' from cli.py — this shim
re-exports the app object so that reference keeps working.
"""
from mailpush.api.server import app  # noqa: F401

__all__ = ["app"]
