// MailPush Dashboard — v1 API + enhanced tabs

const API = '';
const V1 = '/api/v1';

// ── Auth helper ─────────────────────────────────────────────────────────────
function authHeaders(extra = {}) {
  const h = { ...extra };
  if (window.API_TOKEN) h['X-API-Token'] = window.API_TOKEN;
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

// ── In-memory log buffer ─────────────────────────────────────────────────────
const _logs = [];
function addLog(level, msg) {
  const ts = new Date().toLocaleTimeString();
  _logs.unshift({ ts, level, msg });
  if (_logs.length > 200) _logs.pop();
  document.getElementById('log-count').textContent = _logs.length;
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
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
    if (tab === 'logs')       renderLogs();
    if (tab === 'config')     refreshConfig();
  });
});

// ── Health ───────────────────────────────────────────────────────────────────
async function refreshHealth() {
  try {
    const d = await apiJSON('/api/health');
    document.getElementById('uptime').textContent =
      '⏱ ' + Math.floor(d.uptime_seconds / 60) + 'm';
    document.getElementById('acct-count').textContent =
      '🔐 ' + d.accounts_connected + '/' + d.accounts_total;
    const dot = document.getElementById('conn-status');
    dot.className = 'dot' + (d.accounts_connected > 0 ? '' : ' offline');
  } catch (e) {
    document.getElementById('conn-status').className = 'dot offline';
    addLog('error', 'Health check failed: ' + e.message);
  }
}

// ── Overview ─────────────────────────────────────────────────────────────────
async function refreshOverview() {
  try {
    const [state, routes] = await Promise.all([
      apiJSON('/api/state'),
      apiJSON(V1 + '/routes'),
    ]);
    const h = state.status;
    const upSec = h.uptime_seconds;
    const upStr = upSec >= 3600
      ? Math.floor(upSec / 3600) + 'h ' + Math.floor((upSec % 3600) / 60) + 'm'
      : Math.floor(upSec / 60) + 'm ' + Math.floor(upSec % 60) + 's';
    document.getElementById('ov-uptime').textContent = upStr;
    document.getElementById('ov-accounts').textContent =
      h.accounts_connected + ' / ' + h.accounts_total + ' connected';
    document.getElementById('ov-emails').textContent = state.recent_emails;
    document.getElementById('ov-deliveries').textContent =
      (state.deliveries || []).length + ' adapter(s)';
    document.getElementById('ov-webhooks').textContent =
      (state.webhooks || []).length + ' registered';
    document.getElementById('ov-routes').textContent =
      (routes.routes || []).length + ' rule(s)';

    // Recent emails preview
    const emails = await apiJSON('/api/emails?limit=5');
    const el = document.getElementById('ov-email-list');
    if (!emails.emails.length) {
      el.innerHTML = '<div class="empty">No emails yet. Waiting for IDLE push...</div>';
    } else {
      el.innerHTML = emails.emails.map(renderEmailCard).join('');
    }
  } catch (e) {
    addLog('error', 'Overview refresh failed: ' + e.message);
  }
}

// ── Emails ───────────────────────────────────────────────────────────────────
async function refreshEmails() {
  const acct = document.getElementById('email-filter-acct').value;
  let url = V1 + '/emails?limit=50';
  if (acct) url += '&account=' + encodeURIComponent(acct);
  try {
    const d = await apiJSON(url);
    document.getElementById('email-count').textContent = d.total;
    const list = document.getElementById('email-list');
    if (!d.emails.length) {
      list.innerHTML = '<div class="empty">No emails yet. Waiting for IDLE push...</div>';
      return;
    }
    list.innerHTML = d.emails.map((e, i) => renderEmailCard(e, i)).join('');
    // Rebuild account filter
    const sel = document.getElementById('email-filter-acct');
    const seen = new Set(d.emails.map(e => e.account));
    sel.innerHTML = '<option value="">All Accounts</option>' +
      [...seen].map(a =>
        `<option value="${esc(a)}" ${a === acct ? 'selected' : ''}>${esc(a)}</option>`
      ).join('');
  } catch (e) {
    document.getElementById('email-list').innerHTML =
      '<div class="empty">Failed to load. Is the server running?</div>';
    addLog('error', 'Emails refresh failed: ' + e.message);
  }
}

function renderEmailCard(e, idx) {
  const idAttr = idx !== undefined ? `data-idx="${idx}"` : '';
  const redeliverBtn = idx !== undefined
    ? `<button class="btn-sm" onclick="redeliverEmail(${idx})" title="Redeliver">↩ Redeliver</button>`
    : '';
  return `
    <div class="card" ${idAttr}>
      <div class="header">
        <span class="sender">${esc(e.sender)}</span>
        <div style="display:flex;gap:6px;align-items:center">
          <span class="acct-tag">${esc(e.account)}</span>
          ${redeliverBtn}
        </div>
      </div>
      <div class="subject">${esc(e.subject)}</div>
      ${e.subject_cn ? `<div class="subject-cn">🌐 ${esc(e.subject_cn)}</div>` : ''}
      <div class="body-preview">${esc(e.body_preview)}</div>
      ${renderSummary(e.summary)}
      <div class="time">${fmtTime(e.timestamp)}</div>
    </div>`;
}

async function redeliverEmail(idx) {
  try {
    const d = await apiJSON(V1 + `/events/idx-${idx}/redeliver`, { method: 'POST' });
    addLog('info', `Redelivered idx-${idx}: ${d.successful}/${d.total} ok`);
    showToast(`Redelivered: ${d.successful}/${d.total} adapters succeeded`);
  } catch (e) {
    addLog('error', 'Redeliver failed: ' + e.message);
    showToast('Redeliver failed: ' + e.message, true);
  }
}

function renderSummary(s) {
  if (!s) return '';
  const parts = [];
  if (s.ips   && s.ips.length)    parts.push(`IP: ${s.ips.join(' / ')}`);
  if (s.amounts && s.amounts.length) parts.push(s.amounts.join(' / '));
  if (s.urls  && s.urls.length)   parts.push(s.urls[0]);
  if (s.codes && s.codes.length)  parts.push(`🔑 ${s.codes.join(' / ')}`);
  if (!parts.length) return '';
  return '<div class="summary">' +
    parts.map(p => `<span>📝 ${esc(p)}</span>`).join('') +
    '</div>';
}

// ── Accounts ─────────────────────────────────────────────────────────────────
async function refreshAccounts() {
  try {
    const d = await apiJSON(V1 + '/accounts');
    const list = document.getElementById('account-list');
    if (!d.accounts.length) {
      list.innerHTML = '<div class="empty">No accounts configured.</div>';
      return;
    }
    list.innerHTML = d.accounts.map(a => {
      const st = d.status[a.name];
      const connected = st ? st.connected : false;
      return `
        <div class="card">
          <div class="header">
            <span>${connected ? '🟢' : '🔴'} <strong>${esc(a.name)}</strong></span>
            <div style="display:flex;gap:6px">
              <button class="btn-sm" onclick="testAccount('${esc(a.name)}')">🔌 Test</button>
              <button class="btn-sm btn-danger" onclick="removeAccount('${esc(a.name)}')">Remove</button>
            </div>
          </div>
          <div style="font-size:13px;color:var(--dim)">
            ${esc(a.host)}:${a.port} — ${esc(a.username)}
            ${a.has_smtp ? ' · SMTP ✓' : ''}
          </div>
          ${st ? `<div style="font-size:12px;color:var(--dim)">
            Last UID: ${st.last_uid}
            ${st.error ? ' · <span style="color:var(--red)">' + esc(st.error) + '</span>' : ''}
          </div>` : ''}
          <div id="test-result-${esc(a.name)}" class="result-box hidden"></div>
        </div>`;
    }).join('');
  } catch (e) {
    addLog('error', 'Accounts refresh failed: ' + e.message);
  }
}

function showAddAccount()  { document.getElementById('add-account-form').classList.remove('hidden'); }
function hideAddAccount()  { document.getElementById('add-account-form').classList.add('hidden'); }

async function addAccount() {
  const body = {
    name:     document.getElementById('acct-name').value,
    host:     document.getElementById('acct-host').value,
    port:     parseInt(document.getElementById('acct-port').value) || 993,
    username: document.getElementById('acct-user').value,
    password: document.getElementById('acct-pass').value,
  };
  try {
    await apiJSON(V1 + '/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    hideAddAccount();
    refreshAccounts();
    addLog('info', `Account "${body.name}" added`);
  } catch (e) {
    addLog('error', 'Add account failed: ' + e.message);
    showToast('Error: ' + e.message, true);
  }
}

async function removeAccount(name) {
  if (!confirm(`Remove account "${name}"?`)) return;
  try {
    await apiJSON(V1 + '/accounts/' + encodeURIComponent(name), { method: 'DELETE' });
    refreshAccounts();
    addLog('info', `Account "${name}" removed`);
  } catch (e) {
    addLog('error', 'Remove account failed: ' + e.message);
  }
}

async function testAccount(name) {
  const box = document.getElementById('test-result-' + name);
  if (!box) return;
  box.classList.remove('hidden');
  box.textContent = '⏳ Testing…';
  try {
    const d = await apiJSON(V1 + `/accounts/${encodeURIComponent(name)}/test`, { method: 'POST' });
    box.textContent = d.ok
      ? `✅ ${d.message} (${d.latency_ms}ms)`
      : `❌ ${d.message}: ${d.error || ''}`;
    box.className = 'result-box ' + (d.ok ? 'result-ok' : 'result-err');
    addLog(d.ok ? 'info' : 'warn', `Account test "${name}": ${d.message}`);
  } catch (e) {
    box.textContent = '❌ ' + e.message;
    box.className = 'result-box result-err';
    addLog('error', 'Account test failed: ' + e.message);
  }
}

// ── Deliveries ───────────────────────────────────────────────────────────────
async function refreshDeliveries() {
  try {
    const d = await apiJSON(V1 + '/deliveries');
    const list = document.getElementById('delivery-list');
    if (!d.adapters.length) {
      list.innerHTML = '<div class="empty">No delivery adapters configured.</div>';
      return;
    }
    list.innerHTML = d.adapters.map(a => `
      <div class="card">
        <div class="header">
          <span><strong>${esc(a.name)}</strong> <span class="type-tag">${esc(a.type)}</span></span>
          <div style="display:flex;gap:6px">
            <button class="btn-sm" onclick="testDeliveryAdapter('${esc(a.name)}')">🧪 Test</button>
            <button class="btn-sm btn-danger" onclick="removeDelivery('${esc(a.name)}')">Remove</button>
          </div>
        </div>
        <div style="font-size:12px;color:var(--dim);font-family:monospace">
          ${esc(JSON.stringify(a.config))}
        </div>
        <div id="dlv-test-${esc(a.name)}" class="result-box hidden"></div>
      </div>`).join('');
  } catch (e) {
    addLog('error', 'Deliveries refresh failed: ' + e.message);
  }
}

function showAddDelivery()  { document.getElementById('add-delivery-form').classList.remove('hidden'); }
function hideAddDelivery()  { document.getElementById('add-delivery-form').classList.add('hidden'); }

async function addDelivery() {
  let cfg = {};
  try { cfg = JSON.parse(document.getElementById('dlv-config').value || '{}'); }
  catch (e) { showToast('Invalid JSON config', true); return; }
  const body = {
    name:   document.getElementById('dlv-name').value,
    type:   document.getElementById('dlv-type').value,
    config: cfg,
  };
  try {
    await apiJSON(V1 + '/deliveries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    hideAddDelivery();
    refreshDeliveries();
    addLog('info', `Delivery adapter "${body.name}" added`);
  } catch (e) {
    addLog('error', 'Add delivery failed: ' + e.message);
    showToast('Error: ' + e.message, true);
  }
}

async function removeDelivery(name) {
  if (!confirm(`Remove delivery adapter "${name}"?`)) return;
  try {
    await apiJSON(V1 + '/deliveries/' + encodeURIComponent(name), { method: 'DELETE' });
    refreshDeliveries();
    addLog('info', `Delivery "${name}" removed`);
  } catch (e) {
    addLog('error', 'Remove delivery failed: ' + e.message);
  }
}

async function testDeliveryAdapter(name) {
  const box = document.getElementById('dlv-test-' + name);
  if (!box) return;
  box.classList.remove('hidden');
  box.textContent = '⏳ Sending test…';
  try {
    const d = await apiJSON(V1 + `/deliveries/${encodeURIComponent(name)}/test`, { method: 'POST' });
    box.textContent = d.ok ? `✅ ${d.message}` : `❌ ${d.message}`;
    box.className = 'result-box ' + (d.ok ? 'result-ok' : 'result-err');
    addLog(d.ok ? 'info' : 'warn', `Delivery test "${name}": ${d.message}`);
  } catch (e) {
    box.textContent = '❌ ' + e.message;
    box.className = 'result-box result-err';
    addLog('error', 'Delivery test failed: ' + e.message);
  }
}

async function testAllDeliveries() {
  const box = document.getElementById('overview-delivery-result') ||
              document.getElementById('delivery-test-result');
  if (box) {
    box.classList.remove('hidden');
    box.textContent = '⏳ Testing all adapters…';
  }
  try {
    const d = await apiJSON('/api/delivery/test', { method: 'POST' });
    const msg = `${d.ok ? '✅' : '❌'} ${d.message}`;
    if (box) { box.textContent = msg; box.className = 'result-box ' + (d.ok ? 'result-ok' : 'result-err'); }
    addLog('info', 'Test all deliveries: ' + d.message);
    showToast(msg);
  } catch (e) {
    if (box) { box.textContent = '❌ ' + e.message; box.className = 'result-box result-err'; }
    addLog('error', 'Test all deliveries failed: ' + e.message);
  }
}

// ── Routes ───────────────────────────────────────────────────────────────────
async function refreshRoutes() {
  try {
    const d = await apiJSON(V1 + '/routes');
    const list = document.getElementById('route-list');
    if (!d.routes.length) {
      list.innerHTML = '<div class="empty">No route rules. All events go to all adapters.</div>';
      return;
    }
    list.innerHTML = d.routes.map(r => `
      <div class="card">
        <div class="header">
          <span><strong>${esc(r.name || r.id)}</strong> <span class="type-tag">${esc(r.id)}</span></span>
          <button class="btn-sm btn-danger" onclick="removeRoute('${esc(r.id)}')">Remove</button>
        </div>
        <div style="font-size:12px;color:var(--dim)">
          Match: ${esc(JSON.stringify(r.match || {}))}
        </div>
        <div style="font-size:12px;color:var(--accent)">
          → ${(r.adapters || []).map(a => `<code>${esc(a)}</code>`).join(', ')}
        </div>
      </div>`).join('');
  } catch (e) {
    addLog('error', 'Routes refresh failed: ' + e.message);
  }
}

function showAddRoute()  { document.getElementById('add-route-form').classList.remove('hidden'); }
function hideAddRoute()  { document.getElementById('add-route-form').classList.add('hidden'); }

async function addRoute() {
  const name     = document.getElementById('rt-name').value.trim();
  const accounts = document.getElementById('rt-account').value.trim();
  const sender   = document.getElementById('rt-sender').value.trim();
  const subject  = document.getElementById('rt-subject').value.trim();
  const adapters = document.getElementById('rt-adapters').value
    .split(',').map(s => s.trim()).filter(Boolean);

  if (!adapters.length) { showToast('At least one adapter name is required', true); return; }

  const match = {};
  if (accounts) match.account = accounts.split(',').map(s => s.trim()).filter(Boolean);
  if (sender)   match.sender_contains = sender;
  if (subject)  match.subject_contains = [subject];

  const body = { name: name || undefined, match, adapters };
  try {
    await apiJSON(V1 + '/routes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    hideAddRoute();
    refreshRoutes();
    addLog('info', 'Route rule added');
  } catch (e) {
    addLog('error', 'Add route failed: ' + e.message);
    showToast('Error: ' + e.message, true);
  }
}

async function removeRoute(id) {
  if (!confirm(`Remove route rule "${id}"?`)) return;
  try {
    await apiJSON(V1 + '/routes/' + encodeURIComponent(id), { method: 'DELETE' });
    refreshRoutes();
    addLog('info', `Route "${id}" removed`);
  } catch (e) {
    addLog('error', 'Remove route failed: ' + e.message);
  }
}

// ── Webhooks ─────────────────────────────────────────────────────────────────
async function refreshWebhooks() {
  try {
    const d = await apiJSON(V1 + '/webhooks');
    const list = document.getElementById('webhook-list');
    if (!d.webhooks.length) {
      list.innerHTML = '<div class="empty">No webhooks registered.</div>';
      return;
    }
    list.innerHTML = d.webhooks.map(w => `
      <div class="card">
        <div class="header">
          <span><code>${esc(w.id)}</code></span>
          <button class="btn-sm btn-danger" onclick="removeWebhook('${esc(w.id)}')">Remove</button>
        </div>
        <div style="font-size:13px;color:var(--dim)">${esc(w.url)}</div>
        <div style="font-size:11px;color:var(--dim)">Created: ${w.created_at}</div>
      </div>`).join('');
  } catch (e) {
    addLog('error', 'Webhooks refresh failed: ' + e.message);
  }
}

function showAddWebhook()  { document.getElementById('add-webhook-form').classList.remove('hidden'); }
function hideAddWebhook()  { document.getElementById('add-webhook-form').classList.add('hidden'); }

async function addWebhook() {
  const body = {
    url:    document.getElementById('wh-url').value,
    secret: document.getElementById('wh-secret').value || null,
  };
  try {
    await apiJSON(V1 + '/webhooks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    hideAddWebhook();
    refreshWebhooks();
    addLog('info', 'Webhook registered');
  } catch (e) {
    addLog('error', 'Add webhook failed: ' + e.message);
    showToast('Error: ' + e.message, true);
  }
}

async function removeWebhook(id) {
  if (!confirm(`Remove webhook "${id}"?`)) return;
  await apiJSON(V1 + '/webhooks/' + id, { method: 'DELETE' });
  refreshWebhooks();
  addLog('info', `Webhook "${id}" removed`);
}

// ── Logs tab ─────────────────────────────────────────────────────────────────
function refreshLogs() { renderLogs(); }

function renderLogs() {
  const el = document.getElementById('log-list');
  if (!_logs.length) {
    el.innerHTML = '<div class="empty">No log entries yet.</div>';
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

// ── Config ───────────────────────────────────────────────────────────────────
async function refreshConfig() {
  try {
    const d = await apiJSON('/api/config');
    document.getElementById('cfg-translate').checked  = d.translate;
    document.getElementById('cfg-summary').checked    = d.summary;
    document.getElementById('cfg-attachments').checked = d.attachment_info;
    document.getElementById('cfg-merge').checked       = d.merge_batch;
    document.getElementById('cfg-merge-interval').value = d.merge_interval;
  } catch (e) {
    addLog('error', 'Config load failed: ' + e.message);
  }
}

async function saveConfig() {
  const body = {
    translate:        document.getElementById('cfg-translate').checked,
    summary:          document.getElementById('cfg-summary').checked,
    attachment_info:  document.getElementById('cfg-attachments').checked,
    merge_batch:      document.getElementById('cfg-merge').checked,
    merge_interval:   parseInt(document.getElementById('cfg-merge-interval').value) || 30,
    filters:          {},
    smtp_reply_from:  '',
  };
  try {
    await apiJSON('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast('Config saved ✅');
    addLog('info', 'Config saved');
  } catch (e) {
    addLog('error', 'Config save failed: ' + e.message);
    showToast('Error: ' + e.message, true);
  }
}

// ── Toast notification ───────────────────────────────────────────────────────
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

// ── Helpers ──────────────────────────────────────────────────────────────────
function esc(s) {
  if (s === null || s === undefined) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function fmtTime(ts) {
  try { return new Date(ts).toLocaleString(); } catch (e) { return ts; }
}

// ── Init ─────────────────────────────────────────────────────────────────────
refreshHealth();
refreshOverview();
setInterval(refreshHealth, 10000);
setInterval(() => {
  const active = document.querySelector('.tab.active');
  if (!active) return;
  const tab = active.dataset.tab;
  if (tab === 'overview') refreshOverview();
  if (tab === 'emails')   refreshEmails();
}, 15000);
