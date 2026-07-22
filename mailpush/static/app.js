// MailPush Dashboard — v2 中文版 + 登录验证
const API = '';
const STORAGE_KEY = 'mailpush_token';

// ── Auth ──
let API_TOKEN = localStorage.getItem(STORAGE_KEY) || '';

function authHeaders(extra = {}) {
  const h = { ...extra };
  if (API_TOKEN) h['X-API-Token'] = API_TOKEN;
  return h;
}

async function apiFetch(url, opts = {}) {
  return fetch(API + url, {
    ...opts,
    headers: authHeaders(opts.headers || {}),
  });
}

async function apiJSON(url, opts = {}) {
  const r = await apiFetch(url, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(err.error || err.detail || r.statusText);
  }
  return r.json();
}

// ── Login ──
async function handleLogin(e) {
  e.preventDefault();
  const token = document.getElementById('token-input').value.trim();
  const errEl = document.getElementById('login-error');
  if (!token) { errEl.textContent = '请输入 API Token'; return; }
  try {
    const r = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const d = await r.json();
    if (d.ok) {
      API_TOKEN = token;
      localStorage.setItem(STORAGE_KEY, token);
      document.getElementById('login-page').classList.add('hidden');
      document.getElementById('app').classList.remove('hidden');
      initDashboard();
    } else {
      errEl.textContent = 'Token 错误，请重试';
    }
  } catch (e) {
    errEl.textContent = '连接失败: ' + e.message;
  }
}

function handleLogout() {
  API_TOKEN = '';
  localStorage.removeItem(STORAGE_KEY);
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-page').classList.remove('hidden');
  document.getElementById('token-input').value = '';
}

// ── In-memory log buffer ──
const _logs = [];
function addLog(level, msg) {
  const ts = new Date().toLocaleTimeString();
  _logs.unshift({ ts, level, msg });
  if (_logs.length > 200) _logs.pop();
  document.getElementById('log-count').textContent = _logs.length;
  renderLogs();
}

// ── Tabs ──
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    const tab = btn.dataset.tab;
    if (tab === 'overview')   refreshOverview();
    if (tab === 'emails')     refreshEmails();
    if (tab === 'accounts')   refreshAccounts();
    if (tab === 'deliveries') refreshDeliveries();
    if (tab === 'routes')     refreshRoutes();
    if (tab === 'webhooks')   refreshWebhooks();
    if (tab === 'config')     refreshConfig();
  });
});

// ── Health / Overview ──
async function refreshHealth() {
  try {
    const d = await apiJSON('/api/health');
    document.getElementById('uptime').textContent = fmtDuration(d.uptime_seconds);
    document.getElementById('acct-count').textContent = d.accounts_connected + '/' + d.accounts_total + ' 在线';
    const dot = document.getElementById('conn-status');
    dot.className = 'dot ' + (d.accounts_connected > 0 ? 'online' : 'offline');
  } catch (e) { /* ignore */ }
}

function fmtDuration(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h + 'h ' + m + 'm';
}

async function refreshOverview() {
  try {
    const h = await apiJSON('/api/health');
    document.getElementById('ov-uptime').textContent = fmtDuration(h.uptime_seconds);
    document.getElementById('ov-connected').textContent = h.accounts_connected + '/' + h.accounts_total;
    // Also fetch state for emails_today
    const s = await apiJSON('/api/state');
    let today = 0;
    if (s.accounts) {
      for (const a of s.accounts) today += (a.emails_today || 0);
    }
    document.getElementById('ov-today').textContent = today;
    // Account table
    const tbody = document.getElementById('ov-acct-table');
    if (s.accounts && s.accounts.length) {
      tbody.innerHTML = s.accounts.map(a => `
        <tr>
          <td>${esc(a.name)}</td>
          <td><span class="dot ${a.connected ? 'online' : 'offline'}"></span> ${a.connected ? '在线' : '离线'}</td>
          <td>${a.last_event ? new Date(a.last_event).toLocaleString() : '--'}</td>
          <td>${esc(a.error || '')}</td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="4">暂无账户</td></tr>';
    }
  } catch (e) {
    addLog('error', '概览加载失败: ' + e.message);
  }
}

// ── Emails ──
async function refreshEmails() {
  try {
    const d = await apiJSON('/api/emails');
    const el = document.getElementById('email-list');
    if (!d.emails || !d.emails.length) {
      el.innerHTML = '<div class="empty">暂无邮件记录</div>';
      return;
    }
    el.innerHTML = d.emails.map(e => `
      <div class="email-item">
        <div class="email-time">${fmtTime(e.timestamp)}</div>
        <div class="email-from">📧 ${esc(e.account)} — ${esc(e.sender || '')}</div>
        <div class="email-subject">${esc(e.subject)}</div>
        <div class="email-body" onclick="this.style.whiteSpace=this.style.whiteSpace==='pre-wrap'?'nowrap':'pre-wrap'">${esc((e.body_preview || e.body_full || '').slice(0, 500))}</div>
      </div>
    `).join('');
    addLog('info', '加载了 ' + d.emails.length + ' 封邮件');
  } catch (e) {
    document.getElementById('email-list').innerHTML = '<div class="empty">加载失败: ' + esc(e.message) + '</div>';
  }
}

// ── Accounts ──
async function refreshAccounts() {
  try {
    const d = await apiJSON('/api/accounts');
    const accts = d.accounts || d;
    const tbody = document.getElementById('acct-table');
    if (accts.length) {
      tbody.innerHTML = accts.map(a => `
        <tr>
          <td><strong>${esc(a.name)}</strong></td>
          <td>${esc(a.type || 'IMAP')}</td>
          <td>${esc(a.host)}</td>
          <td>${a.port || 993}</td>
          <td>${esc(a.username)}</td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="5">暂无账户</td></tr>';
    }
  } catch (e) {
    addLog('error', '账户加载失败: ' + e.message);
  }
}

// ── Deliveries ──
async function refreshDeliveries() {
  try {
    const d = await apiJSON('/api/delivery');
    const el = document.getElementById('delivery-list');
    const items = d.deliveries || d;
    if (items && items.length) {
      el.innerHTML = items.map(di => `
        <div class="card">
          <h3>🚀 ${esc(di.name || '未命名')}</h3>
          <p>类型: ${esc(di.type)} | 目标: ${esc(di.config?.target || di.config?.url || '--')}</p>
        </div>
      `).join('');
    } else {
      el.innerHTML = '<div class="empty">暂无投递配置</div>';
    }
  } catch (e) {
    document.getElementById('delivery-list').innerHTML = '<div class="empty">加载失败: ' + esc(e.message) + '</div>';
  }
}

// ── Routes ──
async function refreshRoutes() {
  try {
    const d = await apiJSON('/api/state');
    const el = document.getElementById('route-list');
    const routes = d.routes;
    if (routes && routes.length) {
      el.innerHTML = routes.map(r => `
        <div class="card">
          <h3>🔀 ${esc(r.name || '规则')}</h3>
          <p>匹配: ${esc(r.match || '*')} → 投递: ${esc(r.delivery || '默认')}</p>
        </div>
      `).join('');
    } else {
      el.innerHTML = '<div class="empty">暂无路由规则（默认所有邮件走所有投递通道）</div>';
    }
  } catch (e) {
    document.getElementById('route-list').innerHTML = '<div class="empty">加载失败</div>';
  }
}

// ── Webhooks ──
async function refreshWebhooks() {
  try {
    const d = await apiJSON('/api/webhooks');
    const el = document.getElementById('webhook-list');
    if (d.webhooks && d.webhooks.length) {
      el.innerHTML = d.webhooks.map(w => `
        <div class="card">
          <h3>🪝 ${esc(w.name || '未命名')}</h3>
          <p>URL: ${esc(w.url)}</p>
          <p>事件: ${w.events ? esc(w.events.join(', ')) : '所有事件'}</p>
        </div>
      `).join('');
    } else {
      el.innerHTML = '<div class="empty">暂无 Webhook</div>';
    }
  } catch (e) {
    document.getElementById('webhook-list').innerHTML = '<div class="empty">暂无 Webhook</div>';
  }
}

// ── Logs ──
function renderLogs() {
  const el = document.getElementById('log-list');
  if (!_logs.length) {
    el.innerHTML = '<div class="empty">暂无日志</div>';
    return;
  }
  el.innerHTML = _logs.map(l =>
    `<div class="log-entry log-${l.level}">
      <span class="log-ts">${l.ts}</span>
      <span class="log-lvl">${l.level.toUpperCase()}</span>
      <span>${esc(l.msg)}</span>
    </div>`
  ).join('');
}

function clearLogs() {
  _logs.length = 0;
  document.getElementById('log-count').textContent = '0';
  renderLogs();
}

// ── Config ──
async function refreshConfig() {
  try {
    const d = await apiJSON('/api/config');
    document.getElementById('cfg-translate').checked  = d.translate;
    document.getElementById('cfg-summary').checked    = d.summary;
    document.getElementById('cfg-attachments').checked = d.attachment_info;
    document.getElementById('cfg-merge').checked       = d.merge_batch;
    document.getElementById('cfg-merge-interval').value = d.merge_interval;

    const ai = d.ai_summary || {};
    document.getElementById('cfg-ai-enabled').checked    = ai.enabled || false;
    document.getElementById('cfg-ai-provider').value     = ai.provider || 'openai';
    document.getElementById('cfg-ai-model').value        = ai.model || '';
    document.getElementById('cfg-ai-base_url').value     = ai.base_url || '';
    document.getElementById('cfg-ai-api_key').value      = ai.api_key || '';
    document.getElementById('cfg-ai-max_tokens').value   = ai.max_tokens || 200;
    document.getElementById('cfg-ai-prompt').value       = ai.prompt || '';
  } catch (e) {
    addLog('error', '配置加载失败: ' + e.message);
  }
}

async function saveConfig() {
  const body = {
    translate:        document.getElementById('cfg-translate').checked,
    summary:          document.getElementById('cfg-summary').checked,
    attachment_info:  document.getElementById('cfg-attachments').checked,
    merge_batch:      document.getElementById('cfg-merge').checked,
    merge_interval:   parseInt(document.getElementById('cfg-merge-interval').value) || 30,
    ai_summary: {
      enabled:    document.getElementById('cfg-ai-enabled').checked,
      provider:   document.getElementById('cfg-ai-provider').value,
      model:      document.getElementById('cfg-ai-model').value.trim(),
      base_url:   document.getElementById('cfg-ai-base_url').value.trim(),
      api_key:    document.getElementById('cfg-ai-api_key').value.trim(),
      max_tokens: parseInt(document.getElementById('cfg-ai-max_tokens').value) || 200,
      prompt:     document.getElementById('cfg-ai-prompt').value.trim(),
    },
    filters:          {},
    smtp_reply_from:  '',
  };
  try {
    const r = await apiFetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json()).error || '保存失败');
    showToast('设置已保存 ✅');
    addLog('info', '设置已保存');
  } catch (e) {
    addLog('error', '设置保存失败: ' + e.message);
    showToast('错误: ' + e.message, true);
  }
}

// ── Toast ──
function showToast(msg, isError = false) {
  let toast = document.getElementById('_toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = '_toast';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.className = 'toast' + (isError ? ' toast-err' : '');
  toast.style.display = 'block';
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

// ── Helpers ──
function esc(s) {
  if (s === null || s === undefined) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function fmtTime(ts) {
  try { return new Date(ts).toLocaleString(); } catch (e) { return ts; }
}

// ── Init ──
function initDashboard() {
  refreshHealth();
  refreshOverview();
  addLog('info', '面板已加载');
  setInterval(refreshHealth, 10000);
  setInterval(() => {
    const active = document.querySelector('.tab.active');
    if (!active) return;
    const tab = active.dataset.tab;
    if (tab === 'overview') refreshOverview();
    if (tab === 'emails')   refreshEmails();
  }, 15000);
}

// ── Auto-login on page load ──
(async function() {
  if (API_TOKEN) {
    try {
      const r = await fetch('/api/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: API_TOKEN }),
      });
      const d = await r.json();
      if (d.ok) {
        document.getElementById('login-page').classList.add('hidden');
        document.getElementById('app').classList.remove('hidden');
        initDashboard();
        return;
      }
    } catch (e) {}
    // Token expired or invalid, show login
    localStorage.removeItem(STORAGE_KEY);
    API_TOKEN = '';
  }
  // Show login by default
  document.getElementById('login-page').classList.remove('hidden');
})();
