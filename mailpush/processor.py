"""Backward-compatibility shim: mailpush.processor → mailpush.mail.parser"""
from mailpush.mail.parser import parse_email, extract_body, decode_hdr

__all__ = ["parse_email", "extract_body", "decode_hdr"]
