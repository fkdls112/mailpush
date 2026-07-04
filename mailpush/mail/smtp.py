"""SMTP reply sender."""
import smtplib
from email.mime.text import MIMEText


def send_reply(account: dict, to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    """Send an email reply via SMTP. Returns (success, message).

    Supports both flat account dicts (smtp_host/smtp_port/...) and the newer
    smtp sub-object format ({smtp: {host, port, username, password}}).
    """
    # Resolve SMTP config — prefer sub-object, fall back to flat keys
    smtp_cfg = account.get('smtp') or {}
    smtp_host = smtp_cfg.get('host') or account.get('smtp_host')
    if not smtp_host:
        return False, 'Account has no SMTP configured'

    smtp_port = smtp_cfg.get('port') or account.get('smtp_port', 587)
    smtp_user = smtp_cfg.get('username') or account.get('smtp_username') or account.get('username', '')
    smtp_pass = smtp_cfg.get('password') or account.get('smtp_password') or account.get('password', '')

    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = smtp_user
        msg['To'] = to_addr
        msg['Subject'] = subject

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)

        return True, f'Sent via {account["name"]} to {to_addr}'
    except Exception as e:
        return False, str(e)
