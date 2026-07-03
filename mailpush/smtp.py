"""SMTP reply sender."""
import smtplib
from email.mime.text import MIMEText


def send_reply(account: dict, to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    """Send an email reply via SMTP. Returns (success, message)."""
    if not account.get('smtp_host'):
        return False, 'Account has no SMTP configured'

    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = account.get('smtp_username', account.get('username', ''))
        msg['To'] = to_addr
        msg['Subject'] = subject

        smtp_host = account['smtp_host']
        smtp_port = account.get('smtp_port', 587)

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.starttls()
            smtp_user = account.get('smtp_username', account.get('username', ''))
            smtp_pass = account.get('smtp_password', account.get('password', ''))
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)

        return True, f'Sent via {account["name"]} to {to_addr}'
    except Exception as e:
        return False, str(e)
