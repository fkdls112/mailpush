"""Message template rendering — converts MailEvent → human-readable text.

Extracted from mailpush.delivery.dispatcher.render_event so it can be reused
by any layer (delivery adapters, CLI, notifications) without importing the
full dispatcher.
"""
from __future__ import annotations

import re

# ── Auto code-wrap for copyable content ───────────────

# Patterns that should be wrapped in <code> for Telegram tap-to-copy
# Ordered from most specific to least — earlier matches take priority
_RE_CODE_PATTERNS = [
    # URL (http/https) — wrap before other patterns to avoid fragment matching
    (re.compile(r'(https?://[^\s<>\[\]()]+)'), r'<code>\1</code>'),
    # Verification code: preceded by keywords, avoid double-wrapping
    (re.compile(r'(验证码|code|CODE|验证|verification)\s*[：:]\s*([A-Za-z0-9]{4,8})'), r'\1: <code>\2</code>'),
    # Amounts with currency
    (re.compile(r'(¥|￥|\$|€)\s*([\d,]+\.?\d*)'), r'<code>\1\2</code>'),
    # IPv4 address
    (re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'), r'<code>\1</code>'),
    # License plate (Chinese, with optional dash)
    (re.compile(r'([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼][A-Z]-?[A-Z0-9]{5})'), r'<code>\1</code>'),
    # Ticket/order numbers (common prefix + 6+ chars)
    (re.compile(r'((?:SF|JD|YT|EMS|订单号|工单|ticket|order)\s*[：:]?\s*[A-Za-z0-9-]{6,})'), r'<code>\1</code>'),
    # Standalone 6-char codes (last — avoid wrapping already-wrapped or preceded by keyword)
    (re.compile(r'(?<!code>)(?<![A-Za-z0-9])([A-Z0-9]{6})(?![A-Za-z0-9])'), r'<code>\1</code>'),
]


def auto_code_wrap(text: str) -> str:
    """Wrap detected copyable content in <code> tags for Telegram tap-to-copy.

    Detects: IPs, URLs, verification codes, amounts, license plates, order numbers.
    Already-wrapped content (containing <code>) is not double-wrapped.
    """
    if '<code>' in text:
        return text  # already processed
    for pattern, replacement in _RE_CODE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── Event rendering ───────────────────────────────────


def render_event(event) -> str:
    """Render a MailEvent into a human-readable delivery message.

    Handles both Pydantic model instances and plain dicts for flexibility.
    """
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    account = _get(event, 'account', '')
    sender = _get(event, 'sender', '')
    subject = _get(event, 'subject', '')

    lines = [f"📬 {account} — {sender} — {subject}"]

    subject_translated = _get(event, 'subject_translated')
    if subject_translated:
        lines.append(f"  🌐 {subject_translated}")

    s = _get(event, 'summary')
    if s:
        ips = _get(s, 'ips') if isinstance(s, dict) else getattr(s, 'ips', None)
        amounts = _get(s, 'amounts') if isinstance(s, dict) else getattr(s, 'amounts', None)
        urls = _get(s, 'urls') if isinstance(s, dict) else getattr(s, 'urls', None)
        codes = _get(s, 'codes') if isinstance(s, dict) else getattr(s, 'codes', None)
        if ips:
            lines.append(f"  📝 IP: {' / '.join(ips)}")
        if amounts:
            lines.append(f"  📝 {' / '.join(amounts)}")
        if urls:
            lines.append(f"  🔗 {urls[0]}")
        if codes:
            lines.append(f"  📝 验证码: {' / '.join(codes)}")

    attachments = _get(event, 'attachments')
    if attachments:
        names = []
        for a in attachments:
            name = a.get('name', '') if isinstance(a, dict) else getattr(a, 'name', '')
            if name:
                names.append(name)
        if names:
            lines.append(f"  📎 {', '.join(names)}")

    body_preview = _get(event, 'body_preview', '')
    if body_preview:
        preview = body_preview[:120].replace('\n', ' ').strip()
        if preview:
            lines.append(f"  💬 {preview}")

    return '\n'.join(lines)


def render_merged(account_name: str, emails: list, max_show: int = 5) -> str:
    """Render a merged batch notification for multiple emails.

    *emails* should be a list of (sender, subject, body_preview) tuples.
    """
    count = len(emails)
    lines = [f"{account_name} — {count} new emails (merged)"]
    for i, item in enumerate(emails[:max_show]):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            sender, subject = item[0], item[1]
        elif isinstance(item, dict):
            sender, subject = item.get('sender', ''), item.get('subject', '')
        else:
            sender, subject = str(item), ''
        lines.append(f"{i + 1}. {sender} — {subject}")
    if count > max_show:
        lines.append(f"   … and {count - max_show} more")
    return '\n'.join(lines)
