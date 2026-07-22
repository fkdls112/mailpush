"""FastAPI server — REST API, WebSocket, static dashboard."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Configure logging so IMAP module messages are visible
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()],
)

_AUTH_BYPASS_PATHS = frozenset({'/api/health', '/api/auth'})

from mailpush.core import config as config_mgr
from mailpush.mail import imap, smtp as smtp_mod
from mailpush import webhook
from mailpush.delivery import dispatcher as delivery
from mailpush.api.schemas import (
    EmailAccount, WebhookRegistration, WebhookEntry,
    HealthResponse, AccountStatus,
    AppConfig, ReplyRequest, NotifyRequest, EmailNotification, EmailSummary,
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
    # Also persist to SQLite
    _save_email(notification)


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


# ── SQLite email store ────────────────────────────────

_DB_PATH = Path(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))) / 'mailpush' / 'emails.db'


def _init_db() -> sqlite3.Connection:
    """Initialize SQLite store for full email persistence."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            account TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_text TEXT DEFAULT '',
            body_html TEXT DEFAULT '',
            body_preview TEXT DEFAULT '',
            summary TEXT DEFAULT '{}',
            attachments TEXT DEFAULT '[]',
            raw_headers TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_account ON emails(account)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_ts ON emails(timestamp DESC)")
    conn.commit()
    return conn


def _save_email(notification: EmailNotification) -> None:
    """Save a full email notification to SQLite."""
    try:
        conn = _init_db()
        # EmailNotification has: body_full, body_preview
        # Map appropriate fields
        body_text = getattr(notification, 'body_full', '')
        body_preview = getattr(notification, 'body_preview', '')
        summary = getattr(notification, 'summary', EmailSummary())
        attachments = getattr(notification, 'attachments', [])
        conn.execute(
            """INSERT OR IGNORE INTO emails
               (event_id, account, timestamp, sender, subject,
                body_text, body_html, body_preview, summary, attachments, raw_headers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"{notification.account}-{notification.timestamp}",
                notification.account,
                notification.timestamp,
                notification.sender,
                notification.subject,
                body_text,
                '',
                body_preview,
                json.dumps(summary.model_dump() if hasattr(summary, 'model_dump') else summary, ensure_ascii=False),
                json.dumps([a.model_dump() if hasattr(a, 'model_dump') else a for a in attachments], ensure_ascii=False),
                '{}',
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("Failed to save email to SQLite: %s", exc)


def _query_emails(account: Optional[str] = None, limit: int = 50, offset: int = 0, search: str = '') -> list[dict]:
    """Query persisted emails from SQLite."""
    try:
        conn = _init_db()
        where = []
        params = []
        if account:
            where.append("account = ?")
            params.append(account)
        if search:
            where.append("(subject LIKE ? OR sender LIKE ? OR body_text LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where_clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT id, event_id, account, timestamp, sender, subject, body_preview, body_text, summary, attachments, created_at "
            f"FROM emails {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "event_id": r[1],
                "account": r[2],
                "timestamp": r[3],
                "sender": r[4],
                "subject": r[5],
                "body_preview": r[6],
                "body_text": r[7],
                "summary": json.loads(r[8]) if r[8] else {},
                "attachments": json.loads(r[9]) if r[9] else [],
                "created_at": r[10],
            }
            for r in rows
        ]
    except Exception as exc:
        log.warning("Failed to query emails from SQLite: %s", exc)
        return []


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
        entry = {
            'name': acct.get('name'),
            'host': acct.get('host'),
            'port': acct.get('port', 993),
            'username': acct.get('username'),
            'password': '***',
            'ssl': acct.get('ssl', True),
        }
        p = acct.get('proxy')
        if isinstance(p, dict) and p.get('enabled'):
            entry['proxy'] = {
                'enabled': True,
                'type': p.get('type', 'http'),
                'host': p.get('host', ''),
                'port': p.get('port'),
                'username': p.get('username', ''),
                'password': '***' if p.get('password') else '',
            }
        else:
            entry['proxy'] = {'enabled': False}
        safe.append(entry)
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
    # Replace if name exists — preserve secrets when the client sends '***'
    for i, a in enumerate(accounts):
        if a.get('name') == body['name']:
            if body.get('password') == '***':
                body['password'] = a.get('password', '')
            # Preserve proxy password if masked
            new_proxy = body.get('proxy')
            old_proxy = a.get('proxy') or {}
            if isinstance(new_proxy, dict) and new_proxy.get('password') == '***':
                new_proxy['password'] = old_proxy.get('password', '')
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
async def list_emails(account: Optional[str] = None, limit: int = 50, search: str = ''):
    """List recent emails from in-memory buffer (full body from SQLite if available)."""
    results = _recent_emails
    if account:
        results = [e for e in results if e.account == account]
    if search:
        sl = search.lower()
        results = [e for e in results if sl in e.subject.lower() or sl in e.sender.lower()]
    results = results[:max(1, min(limit, 200))]
    return {'emails': results, 'total': len(results)}


@app.get('/api/emails/search')
async def search_emails(account: Optional[str] = None, limit: int = 20, offset: int = 0, q: str = ''):
    """Search persisted emails from SQLite, returns full body_text."""
    return {
        'emails': _query_emails(account=account, limit=limit, offset=offset, search=q),
        'total': len(_query_emails(account=account, limit=9999, search=q)),
    }


@app.get('/api/emails/{email_id}')
async def get_email(email_id: int = PathParam(...)):
    """Get a single persisted email by its SQLite ID, including full body_text."""
    try:
        conn = _init_db()
        row = conn.execute(
            "SELECT id, event_id, account, timestamp, sender, subject, body_text, body_html, body_preview, summary, attachments, raw_headers, created_at "
            "FROM emails WHERE id = ?", (email_id,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(404, f"Email #{email_id} not found")
        return {
            "id": row[0],
            "event_id": row[1],
            "account": row[2],
            "timestamp": row[3],
            "sender": row[4],
            "subject": row[5],
            "body_text": row[6],
            "body_html": row[7],
            "body_preview": row[8],
            "summary": json.loads(row[9]) if row[9] else {},
            "attachments": json.loads(row[10]) if row[10] else [],
            "raw_headers": json.loads(row[11]) if row[11] else {},
            "created_at": row[12],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Config ───────────────────────────────────────────

@app.get('/api/config')
async def get_config():
    cfg = config_mgr.load()
    # Denormalize processing.ai_summary for dashboard convenience
    cfg['ai_summary'] = cfg.get('processing', {}).get('ai_summary', {})
    return _redact_dict(cfg)


@app.put('/api/config')
async def update_config(request: Request):
    cfg = config_mgr.load()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    # If frontend sends ai_summary at top level, move it into processing
    ai_summary = body.pop('ai_summary', None)
    if isinstance(ai_summary, dict):
        processing = body.setdefault('processing', cfg.get('processing', {}))
        processing['ai_summary'] = ai_summary
    cfg.update(body)
    config_mgr.save(cfg)
    return {'ok': True, 'redacted': _redact_dict(cfg)}


@app.post('/api/ai-summary/test')
async def test_ai_summary(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')

    from mailpush.mail.summarizer import summarize_with_ai
    cfg = config_mgr.load()
    default_cfg = cfg.get('processing', {}).get('ai_summary', {})
    ai_cfg = body.get('ai_summary') or body if isinstance(body, dict) and any(k in body for k in ('base_url','model','api_key','enabled','provider')) else default_cfg
    sample = body.get('text') or '测试邮件：你的快递已发货，订单号 SF123456，预计明天到达，请留意查收。'
    summary = await summarize_with_ai(sample, ai_cfg)
    if summary is None:
        raise HTTPException(400, 'AI 摘要未启用或配置不完整')
    return {'ok': True, 'summary': summary}


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
