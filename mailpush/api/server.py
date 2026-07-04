"""FastAPI server — REST API, WebSocket, static dashboard."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Configure logging so IMAP module messages are visible
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)],
)

from mailpush.core import config as config_mgr
from mailpush.mail import imap, smtp as smtp_mod
from mailpush import webhook
from mailpush.delivery import dispatcher as delivery
from mailpush.api.schemas import (
    EmailAccount, WebhookRegistration, WebhookEntry,
    HealthResponse, AccountStatus,
    AppConfig, ReplyRequest, NotifyRequest, EmailNotification,
)

MAX_BODY_SIZE = 64 * 1024   # 64KB max request body

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


# ── Rate limiter ──────────────────────────────────────

class _RateLimiter:
    """Simple in-memory rate limiter: 60 requests per minute per IP."""
    def __init__(self, max_requests: int = 60, window_sec: int = 60):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        now = time.time()
        bucket = self._buckets[ip]
        # Purge expired entries
        cutoff = now - self.window_sec
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True

_rate_limiter = _RateLimiter()


# ── Sensitive field names for config redaction ──────

_SENSITIVE_FIELDS = frozenset({
    'password', 'api_key', 'secret', 'bot_token', 'token',
    'webhook_url', 'smtp_password', 'api_token', 'access_token',
    'auth', 'authorization', 'bearer',
})


def _redact_dict(d: dict) -> dict:
    """Deep-redact sensitive values in a nested dict."""
    out = {}
    for k, v in d.items():
        if k.lower() in _SENSITIVE_FIELDS or any(s in k.lower() for s in ('token', 'secret', 'password', 'key')):
            out[k] = '***'
        elif isinstance(v, dict):
            out[k] = _redact_dict(v)
        elif isinstance(v, list):
            out[k] = [_redact_dict(x) if isinstance(x, dict) else '***' if isinstance(x, str) and len(x) > 40 else x for x in v]
        elif isinstance(v, str) and len(v) > 40:
            out[k] = '***'
        else:
            out[k] = v
    return out


# ── Lifespan ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start IMAP listeners on startup, clean shutdown."""
    cfg = config_mgr.load()
    # Set restrictive permissions on config file
    config_path = Path(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))) / 'mailpush' / 'config.json'
    if config_path.exists():
        os.chmod(config_path, 0o600)

    async def on_email(notification: EmailNotification):
        _add_recent(notification)
        asyncio.create_task(webhook.dispatch(notification))
        # Dispatch through delivery framework (covers legacy + new adapters)
        cfg_current = config_mgr.load()
        if cfg_current.get('delivery_targets') or cfg_current.get('deliveries'):
            asyncio.create_task(delivery.dispatch_event(notification, cfg_current))

    if cfg.get('accounts'):
        asyncio.create_task(
            imap.connect_all(cfg['accounts'], cfg, on_email)
        )

    if cfg.get('api_token'):
        log.info('API authentication enabled (X-API-Token)')
    else:
        raise RuntimeError(
            'api_token must be set in config. Refusing to start.\n'
            'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))"'
        )

    log.info('MailPush API server starting on %s:%s',
             cfg.get('server', {}).get('host', '127.0.0.1'),
             cfg.get('server', {}).get('port', 8080))

    # Log configured deliveries
    configured = delivery.list_configured(cfg)
    if configured:
        log.info('Delivery adapters: %s', ', '.join(d['name'] for d in configured))
    else:
        log.info('No delivery adapters configured')

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
            if not secrets.compare_digest(token, required):
                return JSONResponse(
                    status_code=401,
                    content={'error': 'Missing or invalid X-API-Token header'},
                )
    response = await call_next(request)
    return response

# ── Rate limit middleware ────────────────────────────

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Limit /api/* requests to 60 per minute per client IP."""
    if request.url.path.startswith('/api/') and request.url.path != '/api/health':
        client_ip = request.client.host if request.client else 'unknown'
        if not _rate_limiter.check(client_ip):
            return JSONResponse(
                status_code=429,
                content={'error': 'Rate limit exceeded. Try again later.'},
                headers={'Retry-After': '60'},
            )
    response = await call_next(request)
    return response

# ── Body size middleware ─────────────────────────────

@app.middleware("http")
async def body_size_middleware(request: Request, call_next):
    """Reject oversized request bodies (>1 MB for POST/PUT/PATCH)."""
    if request.method in ('POST', 'PUT', 'PATCH'):
        content_length = request.headers.get('Content-Length')
        if content_length and content_length.isdigit() and int(content_length) > 1024 * 1024:
            return JSONResponse(
                status_code=413,
                content={'error': 'Request body too large. Max 1 MB.'},
            )
    response = await call_next(request)
    return response

# Static dashboard
static_dir = Path(__file__).parent.parent / 'static'
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
    safe = []
    for a in cfg.get('accounts', []):
        safe.append({
            'name': a['name'],
            'host': a['host'],
            'port': a.get('port', 993),
            'username': a['username'],
            'enabled': a.get('enabled', True),
            'type': a.get('type', 'imap'),
            'has_smtp': bool(a.get('smtp_host') or (a.get('smtp') or {}).get('host')),
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
    ok, msg = smtp_mod.send_reply(acct, req.to, req.subject, req.body)
    return {'ok': ok, 'message': msg}


# ── Config ───────────────────────────────────────────

@app.get('/api/config')
async def get_config():
    import copy
    cfg = config_mgr.load()
    safe = _redact_dict(copy.deepcopy(cfg))
    return safe


@app.post('/api/config')
async def update_config(config: AppConfig):
    cfg = config_mgr.load()
    # Merge into processing sub-object (v2 format)
    proc = cfg.setdefault('processing', {})
    proc['translate'] = config.translate
    proc['summary'] = config.summary
    proc['attachment_info'] = config.attachment_info
    proc['merge_batch'] = config.merge_batch
    proc['merge_interval'] = config.merge_interval
    cfg['filters'] = config.filters
    cfg['smtp_reply_from'] = config.smtp_reply_from
    config_mgr.save(cfg)
    return {'ok': True, 'message': 'Config updated'}


# ── Server State ─────────────────────────────────────

@app.get('/api/state')
async def server_state():
    cfg = config_mgr.load()
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
        'deliveries': delivery.list_configured(cfg),
    }


# ── Notify ────────────────────────────────────────────

@app.post('/api/notify')
async def send_notification(req: NotifyRequest):
    """Send a generic notification through all configured delivery adapters."""
    cfg = config_mgr.load()
    if not cfg.get('delivery_targets') and not cfg.get('deliveries'):
        raise HTTPException(400, 'No delivery targets or adapters configured')
    results = await delivery.dispatch_notification(req.message, cfg)
    ok_count = sum(1 for r in results if r.get('ok'))
    return {
        'ok': True,
        'total': len(results),
        'successful': ok_count,
        'results': results,
    }


# ── Delivery Management ──────────────────────────────

@app.get('/api/delivery')
async def list_delivery_adapters():
    """List all configured delivery adapters."""
    cfg = config_mgr.load()
    return {'adapters': delivery.list_configured(cfg)}


@app.post('/api/delivery/test')
async def test_delivery():
    """Send a test message through all configured adapters."""
    cfg = config_mgr.load()
    if not cfg.get('delivery_targets') and not cfg.get('deliveries'):
        raise HTTPException(400, 'No delivery targets or adapters configured')
    results = await delivery.dispatch_notification(
        '🧪 MailPush delivery test — all systems operational.',
        cfg,
    )
    ok_count = sum(1 for r in results if r.get('ok'))
    return {
        'ok': True,
        'message': f'Test sent: {ok_count}/{len(results)} adapters succeeded',
        'results': results,
    }
