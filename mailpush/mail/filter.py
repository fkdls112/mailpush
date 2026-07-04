"""Email filtering — blocklists, allowlists, keyword filters."""


def should_filter(sender: str, subject: str, body: str, filters: dict) -> bool:
    """Return True if email should be filtered out."""
    if not filters:
        return False

    allow_only = filters.get('allow_only_senders', [])
    if allow_only:
        if not any(a.lower() in sender.lower() for a in allow_only):
            return True

    block_senders = filters.get('block_senders', [])
    for b in block_senders:
        if b.lower() in sender.lower():
            return True

    block_keywords = filters.get('block_keywords', [])
    text = (subject + ' ' + body).lower()
    for kw in block_keywords:
        if kw.lower() in text:
            return True

    return False
