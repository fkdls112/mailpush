"""Email subject translation — MyMemory (HTTPS, sanitized before send)."""
import json
import re
import urllib.request
import urllib.parse
from urllib.request import Request

# Patterns to strip before sending to translation API
_SENSITIVE_RE = [
    (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP]'),
    (r'[\w.+-]+@[\w-]+\.[\w.-]+', '[EMAIL]'),
    (r'(?i)(password|passwd|pwd|secret)\s*[:=]\s*\S+', r'\1: [***]'),
    (r'(?i)(token|key|api[_-]?key)\s*[:=]\s*\S{4,}', r'\1: [***]'),
]


def _sanitize(text: str) -> str:
    """Strip sensitive data before sending to external API."""
    for pattern, replacement in _SENSITIVE_RE:
        text = re.sub(pattern, replacement, text)
    return text


def translate(text: str) -> str:
    """Translate English text to Chinese using MyMemory (HTTPS).
       Text is sanitized to remove sensitive data before sending.
       Returns empty string on failure."""
    if not text or not text.strip():
        return ''
    try:
        sanitized = _sanitize(text[:500])
        encoded = urllib.parse.quote(sanitized)
        url = (
            'https://api.mymemory.translated.net/get'
            f'?q={encoded}&langpair=en%7Czh-CN'
        )
        req = Request(url, headers={'User-Agent': 'MailPush/1.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        result = data.get('responseData', {}).get('translatedText', '')
        return result.strip() if result else ''
    except Exception:
        return ''
