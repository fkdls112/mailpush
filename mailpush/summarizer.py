"""Rule-based email summary — extracts IPs, amounts, URLs, codes."""
import re
from .models import EmailSummary


def extract(body: str) -> EmailSummary:
    """Extract structured summary from email body. Zero external API calls."""
    ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', body)
    amounts = re.findall(r'[\$€£¥]\s*[\d,]+\.?\d*', body)
    urls: list[str] = re.findall(r'https?://[^\s<>"]+', body)
    codes = re.findall(r'(?:验证码|code|Code|CODE)[：:\s]*(\w{4,8})', body)

    # Deduplicate while preserving order
    seen_urls: set[str] = set()
    unique_urls: list[str] = []
    for u in urls:
        if u not in seen_urls:
            seen_urls.add(u)
            unique_urls.append(u)

    return EmailSummary(
        ips=list(dict.fromkeys(ips))[:5],
        amounts=list(dict.fromkeys(amounts))[:5],
        urls=unique_urls[:5],
        codes=list(dict.fromkeys(codes))[:5],
    )
