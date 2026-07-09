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

_AUTH_BYPASS_PATHS = frozenset({'/api/health', '/api/auth'})

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
    """Require X-API-Token for all /api/* endpoints (except bypass paths)."""
    if request.url.path.startswith('/api/') and request.url.path not in _AUTH_BYPASS_PATHS:
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

@app.get('/')
@app.get('/dashboard')
async def dashboard(request: Request):
    """Serve the dashboard HTML."""
    cfg = config_mgr.load()
    api_token_configured = bool(cfg.get('api_token', ''))
    
    html_path = static_dir.parent / 'api' / 'templates' / 'dashboard.html'
    if html_path.exists():
        html = html_path.read_text()
    else:
        html = (static_dir / 'index.html').read_text()
    return HTMLResponse(html)

@app.post('/api/auth')
async def api_auth(request: Request):
    """Validate API Token."""
    cfg = config_mgr.load()
    required = cfg.get('api_token', '')
    try:
        body = await request.json()
        token = body.get('token', '')
    except Exception:
        token = ''
    if not required:
        # No token configured — allow access
        return {'ok': True, 'configured': False}
    return {'ok': token == required, 'configured': True}


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
    for acct in cfg.get('accounts', []):
        safe.append({
            'name': acct.get('name'),
            'host': acct.get('host'),
            'port': acct.get('port', 993),
            'username': acct.get('username'),
            'password': '***',
            'ssl': acct.get('ssl', True),
        })
    statuses = imap.get_status()
    for acct in safe:
        name = acct['name']
        if name in statuses:
            s = statuses[name]
            acct['connected'] = s.connected
            acct['last_uid'] = s.last_uid
            acct['last_event'] = s.last_event.isoformat() if s.last_event else None
            acct['error'] = s.error
            acct['emails_today'] = s.emails_today
        else:
            acct['connected'] = False
    return {'accounts': safe}


@app.post('/api/accounts')
async def add_account(request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    required = ('name', 'host', 'username', 'password')
    for field in required:
        if field not in body:
            raise HTTPException(400, f'Missing required field: {field}')
    accounts = cfg.setdefault('accounts', [])
    # Replace if name exists
    for i, a in enumerate(accounts):
        if a.get('name') == body['name']:
            accounts[i] = body
            break
    else:
        accounts.append(body)
    config_mgr.save(cfg)
    return {'ok': True}


@app.patch('/api/accounts/{name}')
async def update_account(name: str, request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    accounts = cfg.setdefault('accounts', [])
    for a in accounts:
        if a.get('name') == name:
            if 'password' in body and body['password'] == '***':
                del body['password']  # Keep existing password
            a.update(body)
            config_mgr.save(cfg)
            return {'ok': True, 'name': a['name']}
    raise HTTPException(404, f'Account "{name}" not found')


@app.delete('/api/accounts/{name}')
async def delete_account(name: str):
    cfg = config_mgr.load()
    accounts = cfg.get('accounts', [])
    cfg['accounts'] = [a for a in accounts if a.get('name') != name]
    config_mgr.save(cfg)
    return {'ok': True}


@app.post('/api/accounts/reconnect')
async def reconnect_accounts():
    cfg = config_mgr.load()
    accounts = cfg.get('accounts', [])
    if not accounts:
        raise HTTPException(400, 'No accounts configured')
    app_state = app.state  # this won't be set at module level
    
    # Cancel existing listeners and reconnect
    from mailpush.mail import imap as imap_mod
    await imap_mod.disconnect_all()
    
    async def on_email(notification: EmailNotification):
        _add_recent(notification)
        asyncio.create_task(webhook.dispatch(notification))
        cfg_current = config_mgr.load()
        if cfg_current.get('delivery_targets') or cfg_current.get('deliveries'):
            asyncio.create_task(delivery.dispatch_event(notification, cfg_current))
    
    asyncio.create_task(imap_mod.connect_all(accounts, cfg, on_email))
    return {'ok': True, 'message': f'Reconnecting {len(accounts)} accounts'}


# ── State / Emails ───────────────────────────────────

@app.get('/api/state')
async def get_state():
    statuses = imap.get_status()
    cfg = config_mgr.load()
    webhooks_list = []  # get_webhooks not available in this version
    return {
        'status': {
            'status': 'ok',
            'version': '1.0.0',
            'uptime_seconds': time.time() - _start_time,
            'accounts_connected': sum(1 for s in statuses.values() if s.connected),
            'accounts_total': len(statuses),
        },
        'accounts': [
            {
                'name': name,
                'connected': s.connected,
                'last_uid': s.last_uid,
                'last_event': s.last_event.isoformat() if s.last_event else None,
                'emails_today': s.emails_today,
                'error': s.error,
            }
            for name, s in statuses.items()
        ],
        'webhooks': webhooks_list,
        'recent_emails': len(_recent_emails),
        'deliveries': delivery.list_configured(cfg),
    }


@app.get('/api/emails')
async def list_emails(account: Optional[str] = None, limit: int = 50):
    results = _recent_emails
    if account:
        results = [e for e in results if e.account == account]
    results = results[:max(1, min(limit, 200))]
    return {'emails': results, 'total': len(results)}


# ── Config ───────────────────────────────────────────

@app.get('/api/config')
async def get_config():
    cfg = config_mgr.load()
    return _redact_dict(cfg)


@app.put('/api/config')
async def update_config(request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    cfg.update(body)
    config_mgr.save(cfg)
    return {'ok': True, 'redacted': _redact_dict(cfg)}


# ── Deliveries ───────────────────────────────────────

@app.get('/api/deliveries')
async def list_deliveries():
    cfg = config_mgr.load()
    return {'deliveries': delivery.list_configured(cfg)}


@app.post('/api/deliveries')
async def add_delivery(request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')

    name = str(body.get('name', '')).strip()
    adapter_type = str(body.get('type', '')).strip()
    adapter_config = body.get('config', {})
    if not name:
        raise HTTPException(400, 'Missing required field: name')
    if adapter_type not in delivery.ADAPTER_CLASSES:
        raise HTTPException(400, f'Unsupported delivery type: {adapter_type}')
    if not isinstance(adapter_config, dict):
        raise HTTPException(400, 'config must be an object')

    deliveries = cfg.setdefault('deliveries', {})
    if not isinstance(deliveries, dict):
        deliveries = {}
        cfg['deliveries'] = deliveries

    entry = {'type': adapter_type}
    entry.update(adapter_config)
    deliveries[name] = entry
    config_mgr.save(cfg)
    return {
        'ok': True,
        'delivery': {
            'name': name,
            'type': adapter_type,
            'config': _redact_dict(adapter_config),
        },
    }


@app.get('/api/deliveries/{name}/test')
async def test_delivery(name: str):
    cfg = config_mgr.load()
    result = await delivery.test_config(name, cfg)
    return {
        'name': name,
        'ok': bool(result.get('ok')),
        'message': result.get('message', ''),
        'error': result.get('error'),
        'result': result,
    }


@app.post('/api/deliveries/{name}/test')
async def test_delivery_content(name: str, request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        body = {}
    body_text = body.get('body', '这是一条来自 MailPush 管理面板的测试通知。')
    notification = {
        'type': 'email.received',
        'account': body.get('account', 'test'),
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'sender': body.get('sender', 'test@example.com'),
        'subject': body.get('subject', 'MailPush 测试通知'),
        'body_preview': str(body_text)[:200],
        'body_text': body_text,
        'summary': {},
        'attachments': [],
    }
    result = await delivery.test_config(name, cfg, notification=notification)
    return {
        'name': name,
        'ok': bool(result.get('ok')),
        'message': result.get('message', ''),
        'error': result.get('error'),
        'result': result,
    }


# ── Webhooks ─────────────────────────────────────────

@app.post('/api/webhooks')
async def register_webhook(req: WebhookRegistration):
    wh = webhook.register(url=str(req.url), secret=req.secret)
    return {'ok': True, 'webhook': {'id': wh.id, 'url': wh.url}}


@app.get('/api/webhooks')
async def list_webhooks():
    return {'webhooks': []}


@app.delete('/api/webhooks/{webhook_id}')
async def delete_webhook(webhook_id: str):
    webhook.unregister(webhook_id)
    return {'ok': True}


# ── Logs ─────────────────────────────────────────────

@app.get('/api/logs')
async def get_logs():
    from mailpush.mail import imap as imap_mod
    return {'logs': imap_mod.get_logs()}


@app.delete('/api/logs')
async def clear_logs():
    from mailpush.mail import imap as imap_mod
    imap_mod.clear_logs()
    return {'ok': True}


# ── Routes ───────────────────────────────────────────

@app.get('/api/routes')
async def list_routes():
    cfg = config_mgr.load()
    routes = cfg.get('routes', [])
    return {'routes': routes}


@app.post('/api/routes')
async def add_route(request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    routes = cfg.setdefault('routes', [])
    routes.append(body)
    config_mgr.save(cfg)
    return {'ok': True}


@app.delete('/api/routes/{index}')
async def delete_route(index: int):
    cfg = config_mgr.load()
    routes = cfg.get('routes', [])
    if 0 <= index < len(routes):
        routes.pop(index)
        config_mgr.save(cfg)
        return {'ok': True}
    raise HTTPException(404, 'Route not found')


# ── Reply ────────────────────────────────────────────

@app.post('/api/reply')
async def api_reply(request: Request):
    """Send a reply via SMTP using the same account."""
    try:
        body = await request.json()
        email_id = body.get('email_id', '')
        account_name = body.get('account', '')
        message = body.get('message', '')
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    if not account_name or not message:
        raise HTTPException(400, 'account and message required')
    cfg = config_mgr.load()
    for acct in cfg.get('accounts', []):
        if acct.get('name') == account_name:
            try:
                result = smtp_mod.send_reply(acct, message, original_email_id=email_id)
                return {'ok': True, 'result': result}
            except Exception as e:
                raise HTTPException(500, str(e))
    raise HTTPException(404, f'Account "{account_name}" not found')


@app.post('/api/notify')
async def api_notify(request: Request):
    """Send a notification email (agent → user)."""
    try:
        body = await request.json()
        account_name = body.get('account', '')
        to = body.get('to', '')
        subject = body.get('subject', 'Notification')
        message = body.get('message', '')
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    cfg = config_mgr.load()
    for acct in cfg.get('accounts', []):
        if acct.get('name') == account_name:
            try:
                result = smtp_mod.send_email(acct, to, subject, message)
                return {'ok': True, 'result': result}
            except Exception as e:
                raise HTTPException(500, str(e))
    raise HTTPException(404, f'Account "{account_name}" not found')
