// MailPush Dashboard

const API = '';

// ── Auth helper ────────────────────────────────────
function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (window.API_TOKEN) headers['X-API-Token'] = window.API_TOKEN;
  if (!headers['Content-Type'] && extra.body && typeof extra.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

async function apiFetch(url, opts = {}) {
  return fetch(API + url, {
    ...opts,
    headers: authHeaders(opts.headers || {}),
  });
}

// ── Tabs ──────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'emails') refreshEmails();
    if (btn.dataset.tab === 'accounts') refreshAccounts();
    if (btn.dataset.tab === 'webhooks') refreshWebhooks();
    if (btn.dataset.tab === 'config') refreshConfig();
  });
});

// ── Health ────────────────────────────────────────
async function refreshHealth() {
  try {
    const r = await fetch(API + '/api/health');
    const d = await r.json();
    document.getElementById('uptime').textContent =
      '⏱ ' + Math.floor(d.uptime_seconds / 60) + 'm';
    document.getElementById('acct-count').textContent =
      '🔐 ' + d.accounts_connected + '/' + d.accounts_total;
    const dot = document.getElementById('conn-status');
    dot.className = 'dot' + (d.accounts_connected > 0 ? '' : ' offline');
  } catch(e) {
    document.getElementById('conn-status').className = 'dot offline';
  }
}

// ── Emails ────────────────────────────────────────
async function refreshEmails() {
  const acct = document.getElementById('email-filter-acct').value;
  let url = API + '/api/emails?limit=50';
  if (acct) url += '&account=' + encodeURIComponent(acct);

  try {
    const r = await apiFetch(url);
    const d = await r.json();
    document.getElementById('email-count').textContent = d.total;

    const list = document.getElementById('email-list');
    if (!d.emails.length) {
      list.innerHTML = '<div class="empty">No emails yet. Waiting for IDLE push...</div>';
      return;
    }

    list.innerHTML = d.emails.map(e => `
      <div class="card">
        <div class="header">
          <span class="sender">${esc(e.sender)}</span>
          <span class="acct-tag">${esc(e.account)}</span>
        </div>
        <div class="subject">${esc(e.subject)}</div>
        ${e.subject_cn ? `<div class="subject-cn">🌐 ${esc(e.subject_cn)}</div>` : ''}
        <div class="body-preview">${esc(e.body_preview)}</div>
        ${renderSummary(e.summary)}
        <div class="time">${fmtTime(e.timestamp)}</div>
      </div>
    `).join('');

    // Update account filter
    const sel = document.getElementById('email-filter-acct');
    const seen = new Set();
    d.emails.forEach(e => seen.add(e.account));
    sel.innerHTML = '<option value="">All Accounts</option>' +
      [...seen].map(a => `<option value="${esc(a)}" ${a === acct ? 'selected' : ''}>${esc(a)}</option>`).join('');
  } catch(e) {
    document.getElementById('email-list').innerHTML =
      '<div class="empty">Failed to load. Is the server running?</div>';
  }
}

function renderSummary(s) {
  if (!s) return '';
  const parts = [];
  if (s.ips && s.ips.length) parts.push(`IP: ${s.ips.join(' / ')}`);
  if (s.amounts && s.amounts.length) parts.push(s.amounts.join(' / '));
  if (s.urls && s.urls.length) parts.push(s.urls[0]);
  if (s.codes && s.codes.length) parts.push(`🔑 ${s.codes.join(' / ')}`);
  if (!parts.length) return '';
  return '<div class="summary">' + parts.map(p => `<span>📝 ${esc(p)}</span>`).join('') + '</div>';
}

// ── Accounts ──────────────────────────────────────
async function refreshAccounts() {
  try {
    const r = await apiFetch('/api/accounts');
    const d = await r.json();
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
            <button onclick="removeAccount('${esc(a.name)}')" style="color:var(--red);background:none;border:none;">Remove</button>
          </div>
          <div style="font-size:13px;color:var(--dim)">
            ${esc(a.host)}:${a.port} — ${esc(a.username)}
            ${a.has_smtp ? ' · SMTP ✓' : ''}
          </div>
          ${st ? `<div style="font-size:12px;color:var(--dim)">Last UID: ${st.last_uid}${st.error ? ' · Error: ' + esc(st.error) : ''}</div>` : ''}
        </div>`;
    }).join('');
  } catch(e) {}
}

function showAddAccount() {
  document.getElementById('add-account-form').classList.remove('hidden');
}
function hideAddAccount() {
  document.getElementById('add-account-form').classList.add('hidden');
}
async function addAccount() {
  const body = {
    name: document.getElementById('acct-name').value,
    host: document.getElementById('acct-host').value,
    port: parseInt(document.getElementById('acct-port').value) || 993,
    username: document.getElementById('acct-user').value,
    password: document.getElementById('acct-pass').value,
  };
  try {
    await apiFetch('/api/accounts', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    hideAddAccount();
    refreshAccounts();
  } catch(e) {}
}
async function removeAccount(name) {
  if (!confirm('Remove account "' + name + '"?')) return;
  await apiFetch('/api/accounts/' + encodeURIComponent(name), { method: 'DELETE' });
  refreshAccounts();
}

// ── Webhooks ──────────────────────────────────────
async function refreshWebhooks() {
  try {
    const r = await apiFetch('/api/webhooks');
    const d = await r.json();
    const list = document.getElementById('webhook-list');
    if (!d.webhooks.length) {
      list.innerHTML = '<div class="empty">No webhooks registered.</div>';
      return;
    }
    list.innerHTML = d.webhooks.map(w => `
      <div class="card">
        <div class="header">
          <span><code>${esc(w.id)}</code></span>
          <button onclick="removeWebhook('${esc(w.id)}')" style="color:var(--red);background:none;border:none;">Remove</button>
        </div>
        <div style="font-size:13px;color:var(--dim)">${esc(w.url)}</div>
        <div style="font-size:11px;color:var(--dim)">Created: ${w.created_at}</div>
      </div>
    `).join('');
  } catch(e) {}
}
function showAddWebhook() {
  document.getElementById('add-webhook-form').classList.remove('hidden');
}
function hideAddWebhook() {
  document.getElementById('add-webhook-form').classList.add('hidden');
}
async function addWebhook() {
  const body = {
    url: document.getElementById('wh-url').value,
    secret: document.getElementById('wh-secret').value || null,
  };
  await apiFetch('/api/webhooks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  hideAddWebhook();
  refreshWebhooks();
}
async function removeWebhook(id) {
  if (!confirm('Remove webhook "' + id + '"?')) return;
  await apiFetch('/api/webhooks/' + id, { method: 'DELETE' });
  refreshWebhooks();
}

// ── Config ────────────────────────────────────────
async function refreshConfig() {
  try {
    const r = await apiFetch('/api/config');
    const d = await r.json();
    document.getElementById('cfg-translate').checked = d.translate;
    document.getElementById('cfg-summary').checked = d.summary;
    document.getElementById('cfg-attachments').checked = d.attachment_info;
    document.getElementById('cfg-merge').checked = d.merge_batch;
    document.getElementById('cfg-merge-interval').value = d.merge_interval;
  } catch(e) {}
}
async function saveConfig() {
  const body = {
    translate: document.getElementById('cfg-translate').checked,
    summary: document.getElementById('cfg-summary').checked,
    attachment_info: document.getElementById('cfg-attachments').checked,
    merge_batch: document.getElementById('cfg-merge').checked,
    merge_interval: parseInt(document.getElementById('cfg-merge-interval').value) || 30,
    filters: {},
    smtp_reply_from: '',
  };
  await apiFetch('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  alert('Config saved!');
}

// ── Helpers ───────────────────────────────────────
function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
function fmtTime(ts) {
  try {
    return new Date(ts).toLocaleString();
  } catch(e) { return ts; }
}

// ── Init ──────────────────────────────────────────
refreshHealth();
refreshEmails();
setInterval(refreshHealth, 10000);
setInterval(refreshEmails, 15000);
