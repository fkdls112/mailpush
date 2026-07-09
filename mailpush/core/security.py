"""Security utilities — HMAC signing, token validation, encryption helpers.

This module is a placeholder for cryptographic / security tooling.
Current implemented utilities:
  - sign_payload(secret, body) → HMAC-SHA256 hex digest
  - verify_signature(secret, body, sig) → bool
  - constant_time_compare(a, b) → bool  (timing-safe)
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def sign_payload(secret: str, body: bytes) -> str:
    """Return HMAC-SHA256 hex digest of *body* signed with *secret*."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature in constant time."""
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, signature)


def constant_time_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(a, b)


def generate_token(nbytes: int = 32) -> str:
    """Generate a cryptographically secure random hex token."""
    return secrets.token_hex(nbytes)


import ipaddress
import socket
from urllib.parse import urlparse

_SSRF_BLOCKED_CIDRS = [
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
]
_METADATA_IP = '169.254.169.254'


def is_safe_url(url: str) -> bool:
    """Check if URL is safe from SSRF (no private IPs, no metadata endpoints)."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    if hostname == _METADATA_IP:
        return False
    if hostname in ('localhost', '0.0.0.0', '127.0.0.1', '::1'):
        return False
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return True  # DNS name, allow (DNS rebinding is a separate issue)
    for net in _SSRF_BLOCKED_CIDRS:
        if addr in net:
            return False
    return True
