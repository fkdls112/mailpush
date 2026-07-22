"""Email summary — rule-based extraction + optional AI-powered summary.

AI summary uses any OpenAI-compatible chat API. The user provides their own
base_url, model and api_key via processing.ai_summary config.

Public API:
    extract(body: str, ai_cfg: Optional[dict]) -> EmailSummary
    summarize_with_ai(text: str, ai_cfg: dict) -> Optional[str]
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from mailpush.core.events import EmailSummary

log = logging.getLogger('mailpush.summarizer')


_DEFAULT_PROMPT = "请总结下面邮件的核心内容："

# ── Rule-based extraction ───────────────────────────────

IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{1,2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{1,2}|[1-9]?\d)\b"
)

URL_RE = re.compile(
    r"https?://[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?\&/=]*)"
)

AMOUNT_RE = re.compile(
    r"(?:[\$€£¥￥]\s*[\d,]+\.?\d*|"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?\s*(?:元|人民币|CNY|RMB|¥|￥)?)"
)

CODE_RE = re.compile(
    r"(?:验证码|校验码|动态码|授权码|密码|code|token|otp)[\s:是为]*"
    r"([A-Za-z0-9]{4,8})",
    re.IGNORECASE,
)


def _extract_structured(body: str) -> dict:
    """Rule-based extraction of IPs, amounts, URLs and verification codes."""
    text = body or ""
    ips = IP_RE.findall(text)
    amounts = AMOUNT_RE.findall(text)
    urls = URL_RE.findall(text)
    codes = CODE_RE.findall(text)

    return {
        'ips': _unique(ips)[:5],
        'amounts': _unique(amounts)[:5],
        'urls': _unique(urls)[:5],
        'codes': _unique(codes)[:5],
    }


def _unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _sanitize_body(body: str) -> str:
    """Prepare body for AI summary: truncate, strip excessive whitespace."""
    if not body:
        return ''
    text = re.sub(r'\s+', ' ', body).strip()
    if len(text) > 4000:
        text = text[:4000] + '…'
    return text


def _build_user_prompt(prompt: str, text: str) -> str:
    base = prompt.strip() if prompt and prompt.strip() else _DEFAULT_PROMPT
    if "{text}" in base:
        return base.replace("{text}", text)
    return f"{base}\n\n{text}"


def _extract_from_reasoning(text: str) -> str:
    """Attempt to pull a likely answer out of reasoning content."""
    # Look for the last Chinese sentence that could be a summary
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        # Pick a short Chinese line that looks like a final answer
        if '\u4e00' <= line <= '\u9fff' and 6 < len(line) < 120:
            return line
    # Fallback: last line overall if it isn't too long
    if lines and len(lines[-1]) < 200:
        return lines[-1]
    return ""


# ── AI summary ──────────────────────────────────────────

_PROVIDER_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "claude": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def _normalize_base_url(base_url: str, provider: str) -> str:
    url = (base_url or "").strip()
    if not url:
        url = _PROVIDER_DEFAULTS.get(provider, "")
    if not url:
        return ""
    url = url.rstrip('/')
    if url.endswith('/chat/completions'):
        url = url[: -len('/chat/completions')]
    if url.endswith('/v1'):
        url += '/chat/completions'
    else:
        url += '/v1/chat/completions'
    return url


async def summarize_with_ai(text: str, ai_cfg: dict) -> Optional[str]:
    """Call OpenAI-compatible chat API to summarize text asynchronously.

    Returns None if disabled, misconfigured, or the call fails.
    """
    if not isinstance(ai_cfg, dict):
        return None
    if not ai_cfg.get('enabled'):
        return None

    api_key = (ai_cfg.get('api_key') or '').strip()
    model = (ai_cfg.get('model') or '').strip()
    if not api_key or not model:
        log.debug('AI summary enabled but api_key or model missing')
        return None

    provider = (ai_cfg.get('provider') or 'openai').strip()
    base_url = _normalize_base_url(ai_cfg.get('base_url') or '', provider)
    if not base_url:
        return None

    max_tokens = ai_cfg.get('max_tokens', 500)
    try:
        max_tokens = int(max_tokens)
    except Exception:
        max_tokens = 500
    if max_tokens < 50:
        max_tokens = 50

    sanitized = _sanitize_body(text)
    if not sanitized:
        return None

    user_prompt = _build_user_prompt(ai_cfg.get('prompt', ''), sanitized)

    payload = {
        'model': model,
        'messages': [
            {'role': 'user', 'content': user_prompt},
        ],
        'max_tokens': max_tokens,
    }

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(base_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get('choices', [])
        if not choices:
            log.warning('AI summary empty choices')
            return None
        message = choices[0].get('message', {})
        content = message.get('content', '')
        if not content:
            content = message.get('reasoning_content', '')
            if content:
                content = _extract_from_reasoning(content)
        content = content.strip() if content else None
        return content
    except httpx.HTTPStatusError as exc:
        log.warning('AI summary HTTP %d: %s', exc.response.status_code, exc.response.text[:200])
        return None
    except Exception as e:
        log.warning('AI summary failed: %s', e)
        return None


# ── Main entry point ────────────────────────────────────

async def extract(body: str, ai_cfg: Optional[dict] = None) -> EmailSummary:
    """Extract structured summary from email body, optionally with AI summary."""
    structured = _extract_structured(body)
    ai_text = await summarize_with_ai(body or '', ai_cfg or {}) if ai_cfg else None
    return EmailSummary(
        ips=structured['ips'],
        amounts=structured['amounts'],
        urls=structured['urls'],
        codes=structured['codes'],
        ai_summary=ai_text,
    )


def extract_sync(body: str, ai_cfg: Optional[dict] = None) -> EmailSummary:
    """Synchronous wrapper for back-compat / non-async contexts."""
    import asyncio
    try:
        return asyncio.run(extract(body, ai_cfg))
    except RuntimeError:
        # If already inside an event loop, fallback to rule extraction only.
        structured = _extract_structured(body)
        return EmailSummary(
            ips=structured['ips'],
            amounts=structured['amounts'],
            urls=structured['urls'],
            codes=structured['codes'],
            ai_summary=None,
        )
