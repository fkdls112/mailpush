"""FastAPI server — REST API, WebSocket, static dashboard."""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Configure logging so IMAP module messages are visible
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)],
)

from . import config as config_mgr
from . import imap, smtp, webhook
from .models import (
    EmailAccount, WebhookRegistration, WebhookEntry,
    HealthResponse, AccountStatus, EmailsQuery,
    AppConfig, ReplyRequest, NotifyRequest, EmailNotification,
)

log = logging.getLogger('mailpush.server')

# ── Shared state ─────────────────────────────────────

_recent_emails: list[EmailNotification] = []
"""Ring buffer of recent email notifications (max 500)."""
MAX_RECENT = 500
_start_time = time.time()


def _add_recent(notification: EmailNotification) -> None:
    _recent_emails.insert(0, notification)
    if len(_recent_emails) > MAX_RECENT:
        _recent_emails.pop()


def _format_email(notification: EmailNotification) -> str:
    """Format email notification for delivery via hermes send."""
    lines = []
    lines.append(f"📬 {notification.account} — {notification.sender} — {notification.subject}")
    if notification.subject_cn:
        lines.append(f"  🌐 {notification.subject_cn}")
    s = notification.summary
    if s:
        if s.ips:
            lines.append(f"  📝 IP: {' / '.join(s.ips)}")
        if s.amounts:
            lines.append(f"  📝 {' / '.join(s.amounts)}")
        if s.urls:
            lines.append(f"  📝 {s.urls[0]}")
        if s.codes:
            lines.append(f"  📝 验证码: {' / '.join(s.codes)}")
    if notification.attachments:
        names = [a['name'] for a in notification.attachments]
        lines.append(f"  📎 {', '.join(names)}")
    return '\n'.join(lines)


async def _push_notification(notification: EmailNotification, targets: list[str]) -> None:
    """Push email notification to all configured delivery targets via hermes send."""
    message = _format_email(notification)
    HERMES = os.path.expanduser('~/.local/bin/hermes')
    for target in targets:
        try:
            proc = await asyncio.create_subprocess_exec(
                HERMES, 'send', '--to', target, message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                log.info('pushed to %s: %s — %s', target.split(':')[0], notification.account, notification.subject[:40])
            else:
                log.error('hermes send to %s failed: %s', target, stderr.decode().strip())
        except Exception as e:
            log.error('hermes send to %s error: %s', target, e)

async def _send_notify(message: str, targets: list[str]) -> list[str]:
    """Send a generic notification to all delivery targets. Returns list of targets sent to."""
    HERMES = os.path.expanduser('~/.local/bin/hermes')
    results = []
    for target in targets:
        try:
            proc = await asyncio.create_subprocess_exec(
                HERMES, 'send', '--to', target, message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                log.info('notify sent to %s', target.split(':')[0])
                results.append(target)
            else:
                log.error('notify to %s failed: %s', target, stderr.decode().strip())
        except Exception as e:
            log.error('notify to %s error: %s', target, e)
    return results



# ── Lifespan ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start IMAP listeners on startup, clean shutdown."""
    cfg = config_mgr.load()
    # Set restrictive permissions on config file
    from pathlib import Path as P
    config_path = P(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))) / 'mailpush' / 'config.json'
    if config_path.exists():
        os.chmod(config_path, 0o600)

    async def on_email(notification: EmailNotification):
        _add_recent(notification)
        asyncio.create_task(webhook.dispatch(notification))
        targets = cfg.get('delivery_targets', [])
        if targets:
            asyncio.create_task(_push_notification(notification, targets))

    if cfg.get('accounts'):
        asyncio.create_task(
            imap.connect_all(cfg['accounts'], cfg, on_email)
        )

    if cfg.get('api_token'):
        log.info('API authentication enabled (X-API-Token)')
    else:
        log.warning('No api_token set in config — API is unauthenticated!')

    log.info('MailPush API server starting on %s:%s',
             cfg.get('server', {}).get('host', '127.0.0.1'),
             cfg.get('server', {}).get('port', 8080))

    yield


# ── App ──────────────────────────────────────────────

app = FastAPI(
    title='MailPush API',
    description='Real-time email push with IMAP IDLE — designed for AI agent integration',
    version='1.0.0',
    lifespan=lifespan,
)

# ── Auth middleware ──────────────────────────────────

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require X-API-Token for all /api/* endpoints (except /api/health)."""
    if request.url.path.startswith('/api/') and request.url.path != '/api/health':
        cfg = config_mgr.load()
        required = cfg.get('api_token', '')
        if required:
            token = request.headers.get('X-API-Token', '')
            if token != required:
                return JSONResponse(
                    status_code=401,
                    content={'error': 'Missing or invalid X-API-Token header'},
                )
    response = await call_next(request)
    return response

# Static dashboard
static_dir = Path(__file__).parent / 'static'
if static_dir.exists():
    app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')


# ── Dashboard ────────────────────────────────────────

@app.get('/', response_class=HTMLResponse)
async def dashboard():
    if not static_dir.exists():
        return '<h1>MailPush API</h1><p>Dashboard not available. Use <a href="/docs">/docs</a> for API.</p>'
    cfg = config_mgr.load()
    token = cfg.get('api_token', '')
    html = (static_dir / 'index.html').read_text()
    # Inject API token so dashboard JS can authenticate
    if token:
        html = html.replace('</head>', f'<script>window.API_TOKEN = {json.dumps(token)};</script></head>')
    return HTMLResponse(html)


# ── Health ───────────────────────────────────────────

@app.get('/api/health', response_model=HealthResponse)
async def health():
    statuses = imap.get_status()
    return HealthResponse(
        status='ok',
        version='1.0.0',
        uptime_seconds=time.time() - _start_time,
        accounts_connected=sum(1 for s in statuses.values() if s.connected),
        accounts_total=len(statuses),
    )


# ── Accounts ─────────────────────────────────────────

@app.get('/api/accounts')
async def list_accounts():
    cfg = config_mgr.load()
    # Return without passwords
    safe = []
    for a in cfg.get('accounts', []):
        safe.append({
            'name': a['name'],
            'host': a['host'],
            'port': a.get('port', 993),
            'username': a['username'],
            'has_smtp': bool(a.get('smtp_host')),
        })
    return {'accounts': safe, 'status': imap.get_status()}


@app.post('/api/accounts')
async def add_account(account: EmailAccount):
    cfg = config_mgr.load()
    names = [a['name'] for a in cfg.get('accounts', [])]
    if account.name in names:
        raise HTTPException(409, f'Account "{account.name}" already exists')
    cfg.setdefault('accounts', []).append(account.model_dump())
    config_mgr.save(cfg)
    return {'ok': True, 'message': f'Account "{account.name}" added. Restart server to activate.'}


@app.delete('/api/accounts/{name}')
async def remove_account(name: str):
    cfg = config_mgr.load()
    cfg['accounts'] = [a for a in cfg.get('accounts', []) if a['name'] != name]
    config_mgr.save(cfg)
    return {'ok': True, 'message': f'Account "{name}" removed. Restart server to apply.'}


# ── Emails ───────────────────────────────────────────

@app.get('/api/emails')
async def list_emails(
    account: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    since: Optional[str] = Query(None),
):
    results = _recent_emails
    if account:
        results = [e for e in results if e.account == account]
    if since:
        results = [e for e in results if e.timestamp > since]
    return {
        'count': len(results[:limit]),
        'total': len(results),
        'emails': results[:limit],
    }


# ── Webhooks ─────────────────────────────────────────

@app.get('/api/webhooks')
async def list_webhooks():
    return {'webhooks': webhook.list_all()}


@app.post('/api/webhooks', response_model=WebhookEntry)
async def register_webhook(req: WebhookRegistration):
    try:
        return webhook.register(req.url, req.secret)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete('/api/webhooks/{wid}')
async def remove_webhook(wid: str):
    if webhook.unregister(wid):
        return {'ok': True}
    raise HTTPException(404, f'Webhook "{wid}" not found')


# ── Reply ────────────────────────────────────────────

@app.post('/api/reply')
async def send_reply(req: ReplyRequest):
    cfg = config_mgr.load()
    acct = next((a for a in cfg.get('accounts', []) if a['name'] == req.account), None)
    if not acct:
        raise HTTPException(404, f'Account "{req.account}" not found')
    ok, msg = smtp.send_reply(acct, req.to, req.subject, req.body)
    return {'ok': ok, 'message': msg}


# ── Config ───────────────────────────────────────────

@app.get('/api/config')
async def get_config():
    cfg = config_mgr.load()
    # Redact passwords
    safe = dict(cfg)
    for a in safe.get('accounts', []):
        a['password'] = '***'
        a['smtp_password'] = '***'
    return safe


@app.post('/api/config')
async def update_config(config: AppConfig):
    cfg = config_mgr.load()
    # Merge non-account fields
    cfg['translate'] = config.translate
    cfg['summary'] = config.summary
    cfg['attachment_info'] = config.attachment_info
    cfg['merge_batch'] = config.merge_batch
    cfg['merge_interval'] = config.merge_interval
    cfg['filters'] = config.filters
    cfg['smtp_reply_from'] = config.smtp_reply_from
    config_mgr.save(cfg)
    return {'ok': True, 'message': 'Config updated'}


# ── Server State ─────────────────────────────────────

@app.get('/api/state')
async def server_state():
    health_data = HealthResponse(
        status='ok',
        version='1.0.0',
        uptime_seconds=time.time() - _start_time,
        accounts_connected=sum(1 for s in imap.get_status().values() if s.connected),
        accounts_total=len(imap.get_status()),
    )
    return {
        'status': health_data.model_dump(),
        'accounts': [s.model_dump() for s in imap.get_status().values()],
        'webhooks': webhook.list_all(),
        'recent_emails': len(_recent_emails),
    }


# ── Notify ────────────────────────────────────────────

@app.post('/api/notify')
async def send_notification(req: NotifyRequest):
    """Send a generic notification to all configured delivery_targets.

    External programs can POST here to relay messages to the user's
    preferred platforms (WeChat, Telegram, etc.).

    Example: curl -X POST http://127.0.0.1:8080/api/notify \\
      -H 'X-API-Token: your-token' \\
      -H 'Content-Type: application/json' \\
      -d '{"message": "Build passed: v2.3.1"}'
    """
    cfg = config_mgr.load()
    targets = cfg.get('delivery_targets', [])
    if not targets:
        raise HTTPException(400, 'No delivery_targets configured')
    results = await _send_notify(req.message, targets)
    return {'ok': True, 'sent_to': results}
