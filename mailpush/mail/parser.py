"""Email body extraction — text/plain, HTML fallback, forwarded-header stripping."""
import re
from email import policy
from email.parser import BytesParser
from email.header import decode_header


def decode_hdr(s) -> str:
    """Decode email header to string."""
    if s is None:
        return ''
    parts = decode_header(s)
    result = ''
    for part, charset in parts:
        if isinstance(part, bytes):
            result += part.decode(charset or 'utf-8', errors='replace')
        else:
            result += str(part)
    return result


def extract_body(msg) -> str:
    """Extract clean text body from email message object."""
    body = ''
    html_body = ''

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                charset = part.get_content_charset() or 'utf-8'
                b = part.get_payload(decode=True)
                if b:
                    body = b.decode(charset, errors='replace')
            elif ct == 'text/html' and not html_body:
                charset = part.get_content_charset() or 'utf-8'
                b = part.get_payload(decode=True)
                if b:
                    html_body = b.decode(charset, errors='replace')
    else:
        charset = msg.get_content_charset() or 'utf-8'
        b = msg.get_payload(decode=True)
        if b:
            body = b.decode(charset, errors='replace')

    # HTML fallback if text/plain is too short
    if (not body or len(body.replace('\r\n', '\n').strip().split('\n')) < 3) and html_body:
        body = re.sub(r'<style[^>]*>.*?</style>', '', html_body, flags=re.DOTALL)
        body = re.sub(r'<[^>]+>', '\n', body)
        body = re.sub(r'&nbsp;', ' ', body)
        body = re.sub(r'&gt;', '>', body)
        body = re.sub(r'&lt;', '<', body)
        body = re.sub(r'&amp;', '&', body)
        body = re.sub(r'\n{3,}', '\n\n', body)

    # Clean up
    if body:
        body = body.replace('\r\n', '\n').strip()
        # Strip forwarded headers
        body = re.sub(r'^---Original---\n(?:.*\n){1,8}', '', body)
        body = re.sub(r'\n{3,}', '\n\n', body).strip()

    return body


def parse_email(raw_bytes: bytes) -> tuple:
    """Parse raw email bytes. Returns (sender, subject, body, attachments_info)."""
    parser = BytesParser(policy=policy.default)
    msg = parser.parsebytes(raw_bytes)

    sender = decode_hdr(msg.get('From', ''))
    subject = decode_hdr(msg.get('Subject', ''))
    body = extract_body(msg)

    # Attachments
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            disp = part.get_content_disposition()
            if disp == 'attachment' or (disp is None and part.get_content_maintype() not in ('text', 'multipart')):
                fname = part.get_filename()
                if fname:
                    fname = decode_hdr(fname)
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                    attachments.append({'name': fname, 'size': size})

    return sender, subject, body, attachments
