"""IMAP IDLE listener — real-time email monitoring for multiple accounts."""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

import aioimaplib

from mailpush.mail import parser as processor
from mailpush.mail.filter import should_filter
from mailpush.mail import summarizer, translator
from mailpush.core.events import EmailNotification, AccountStatus

log = logging.getLogger('mailpush.imap')

# ── Module state ─────────────────────────────────────

_account_statuses: dict[str, AccountStatus] = {}
_email_count_today: dict[str, int] = {}
_state_file: Path = Path.home() / '.config' / 'mailpush' / 'state.json'


def _load_state() -> dict:
    try:
        if _state_file.exists():
            return json.loads(_state_file.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    _state_file.parent.mkdir(parents=True, exist_ok=True)
    _state_file.write_text(json.dumps(state))


# ── Public API ───────────────────────────────────────

OnEmailCallback = Callable[[EmailNotification], Awaitable[None]]
"""Callback invoked when a new email is processed. Receives EmailNotification."""


def get_status() -> dict[str, AccountStatus]:
    """Return current status of all monitored accounts."""
    return dict(_account_statuses)


def _get_processing(cfg: dict) -> dict:
    """Extract processing config, supporting both nested and flat formats."""
    proc = cfg.get('processing', {})
    if not proc:
        # Fall back to flat top-level keys (legacy format)
        proc = {
            'translate': cfg.get('translate', False),
            'summary': cfg.get('summary', True),
            'attachment_info': cfg.get('attachment_info', True),
            'merge_batch': cfg.get('merge_batch', True),
            'merge_interval': cfg.get('merge_interval', 30),
            'body_max_chars': cfg.get('body_max_chars', 0),
        }
    return proc


async def connect_all(
    accounts: list[dict],
    cfg: dict,
    on_email: OnEmailCallback,
) -> None:
    """Start IMAP IDLE connections for all accounts.

    Args:
        accounts: List of account dicts {name, host, port, username, password, smtp_*}
        cfg: App config (processing sub-object or flat keys, filters)
        on_email: Async callback invoked with EmailNotification for each new email
    """
    state = _load_state()
    proc = _get_processing(cfg)
    tasks = []

    for acct in accounts:
        if not acct.get('enabled', True):
            log.info('Account %s disabled — skipping', acct.get('name', '?'))
            continue
        task = asyncio.create_task(
            _watch_account(acct, state, cfg, on_email)
        )
        tasks.append(task)
        _account_statuses[acct['name']] = AccountStatus(
            name=acct['name'],
            connected=False,
            last_uid=state.get(acct['name'], 0),
        )

    # Merge timer
    merge_tasks = []
    if proc.get('merge_batch', True):
        merge_tasks.append(asyncio.create_task(
            _merge_timer(proc.get('merge_interval', 30), on_email)
        ))

    await asyncio.gather(*tasks, *merge_tasks)


# ── Merge queue ──────────────────────────────────────

_merge_queue: dict[str, list] = {}       # account_name -> [(sender, subject, body, atts), ...]
_merge_timers: dict[str, asyncio.Task] = {}


async def _merge_timer(interval: int, on_email: OnEmailCallback):
    """Periodically flush merge queues."""
    while True:
        await asyncio.sleep(5)
        for name in list(_merge_queue):
            if not _merge_queue[name]:
                continue
            batch = _merge_queue[name]
            _merge_queue[name] = []
            count = len(batch)
            if count == 1:
                sender, subject, body, atts = batch[0]
                await _build_and_push(name, [(sender, subject, body, atts)], {}, on_email)
            else:
                await _build_merged(name, batch, {}, on_email)


# ── Account watcher ──────────────────────────────────

async def _watch_account(
    acct: dict,
    state: dict,
    cfg: dict,
    on_email: OnEmailCallback,
) -> None:
    """Main loop for a single IMAP account."""
    name = acct['name']
    host = acct['host']
    port = acct.get('port', 993)
    user = acct['username']
    pwd = acct['password']
    last_uid = state.get(name, 0)

    while True:
        try:
            imap = aioimaplib.IMAP4_SSL(host, port, timeout=15)
            await asyncio.wait_for(imap.wait_hello_from_server(), 15)
            await asyncio.wait_for(imap.login(user, pwd), 15)
            await asyncio.wait_for(imap.select('INBOX'), 15)

            # Initialize UID on first connect
            if last_uid == 0:
                result, data = await asyncio.wait_for(imap.uid_search('ALL'), 15)
                if data and data[0]:
                    last_uid = max(int(u) for u in data[0].split())
                state[name] = last_uid
                _save_state(state)

            _account_statuses[name] = AccountStatus(
                name=name, connected=True, last_uid=last_uid,
                last_event=datetime.now(timezone.utc),
            )
            log.info('%s: connected, IDLE listening', name)

            # ── Catch up on missed emails (backlog check on reconnect) ──
            try:
                result, data = await asyncio.wait_for(imap.uid_search('ALL'), 15)
                if data and data[0]:
                    current_max = max(int(u) for u in data[0].split())
                    if current_max > last_uid:
                        log.info('%s: backlog detected — %d emails since UID %d', name, current_max - last_uid, last_uid)
                        new_uids = range(last_uid + 1, current_max + 1)
                        backlog_emails = []
                        for uid in new_uids:
                            try:
                                result, msg_data = await asyncio.wait_for(
                                    imap.uid('fetch', str(uid), '(BODY.PEEK[])'), 10)
                                raw = _extract_raw(msg_data)
                                if raw:
                                    sender, subject, body, atts = processor.parse_email(raw)
                                    backlog_emails.append((sender, subject, body, atts))
                            except Exception as e:
                                log.error('%s: backlog fetch UID=%d failed: %s', name, uid, e)
                        if backlog_emails:
                            log.info('%s: processing %d backlog emails', name, len(backlog_emails))
                            proc = _get_processing(cfg)
                            merge = proc.get('merge_batch', True)
                            if merge:
                                _merge_queue.setdefault(name, []).extend(backlog_emails)
                            else:
                                await _build_and_push(name, backlog_emails, cfg, on_email)
                        last_uid = current_max
                        state[name] = last_uid
                        _save_state(state)
                        _account_statuses[name] = AccountStatus(
                            name=name, connected=True, last_uid=last_uid,
                            last_event=datetime.now(timezone.utc),
                        )
            except Exception as e:
                log.warning('%s: backlog check failed: %s', name, e)

            # ── IDLE loop ─────────────────────────────
            while True:
                await asyncio.wait_for(imap.idle_start(timeout=300), 15)
                try:
                    await asyncio.wait_for(imap.wait_server_push(timeout=300), 305)
                    imap.idle_done()
                    await asyncio.sleep(0.5)

                    # Check for new emails
                    result, data = await asyncio.wait_for(imap.uid_search('ALL'), 15)
                    if data and data[0]:
                        current_max = max(int(u) for u in data[0].split())
                        if current_max > last_uid:
                            new_uids = range(last_uid + 1, current_max + 1)
                            emails = []

                            for uid in new_uids:
                                try:
                                    result, msg_data = await asyncio.wait_for(
                                        imap.uid('fetch', str(uid), '(BODY.PEEK[])'), 10)
                                    raw = _extract_raw(msg_data)
                                    if raw:
                                        sender, subject, body, atts = processor.parse_email(raw)
                                        emails.append((sender, subject, body, atts))
                                except Exception as e:
                                    log.error('%s: fetch UID=%d failed: %s', name, uid, e)

                            if emails:
                                log.info('%s: %d new email(s)', name, len(emails))
                                proc = _get_processing(cfg)
                                merge = proc.get('merge_batch', True)
                                if merge:
                                    _merge_queue.setdefault(name, []).extend(emails)
                                    if len(emails) > 1:
                                        log.info('%s: %d emails queued for merge', name, len(emails))
                                else:
                                    await _build_and_push(name, emails, cfg, on_email)

                            last_uid = current_max
                            state[name] = last_uid
                            _save_state(state)

                except asyncio.TimeoutError:
                    imap.idle_done()
                except Exception as e:
                    log.warning('%s: IDLE error: %s', name, e)
                    imap.idle_done()
                    raise  # reconnect

        except Exception as e:
            _account_statuses[name] = AccountStatus(
                name=name, connected=False, last_uid=last_uid, error=str(e),
            )
            log.warning('%s: disconnected, reconnecting in 30s: %s', name, e)
            await asyncio.sleep(30)


# ── Helpers ──────────────────────────────────────────

def _extract_raw(msg_data) -> bytes | None:
    """Extract raw email bytes from IMAP fetch response."""
    if not msg_data:
        return None
    for item in msg_data:
        if isinstance(item, (list, tuple)):
            for x in item:
                if isinstance(x, (bytes, bytearray)) and len(x) > 100:
                    return bytes(x)
        elif isinstance(item, (bytes, bytearray)) and len(item) > 100:
            return bytes(item)
    return None


async def _build_and_push(
    account_name: str,
    emails: list,
    cfg: dict,
    on_email: OnEmailCallback,
) -> None:
    """Process emails and invoke callback."""
    proc = _get_processing(cfg)
    translate_enabled = proc.get('translate', False)
    summary_enabled = proc.get('summary', True)
    body_max_chars = proc.get('body_max_chars', 0)
    filters_cfg = cfg.get('filters', {})

    for sender, subject, body, atts in emails:
        # Filter
        if should_filter(sender, subject, body, filters_cfg):
            log.info('%s: filtered – %s – %s', account_name, sender, subject[:30])
            continue

        # Truncate body if configured
        if body_max_chars and body and len(body) > body_max_chars:
            body = body[:body_max_chars]

        # Translate
        subject_cn = None
        if translate_enabled and not re.search(r'[\u4e00-\u9fff]', subject):
            cn = translator.translate(subject)
            if cn and cn != subject:
                subject_cn = cn

        # Summary
        summary_data = summarizer.extract(body) if summary_enabled else None

        # Build notification
        notification = EmailNotification(
            account=account_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            sender=sender,
            subject=subject,
            subject_cn=subject_cn,
            body_preview=body[:200] if body else '',
            body_full=body,
            summary=summary_data or summarizer.extract(''),
            attachments=atts,
        )

        await on_email(notification)


async def _build_merged(
    account_name: str,
    batch: list,
    cfg: dict,
    on_email: OnEmailCallback,
) -> None:
    """Build merged notification for multiple emails."""
    proc = _get_processing(cfg)
    translate_enabled = proc.get('translate', False)
    summary_enabled = proc.get('summary', True)
    filters_cfg = cfg.get('filters', {})

    filtered = []
    for sender, subject, body, atts in batch:
        if not should_filter(sender, subject, body, filters_cfg):
            filtered.append((sender, subject, body, atts))

    if not filtered:
        return

    lines = [f'{account_name} — {len(filtered)} new emails (merged)']
    for i, (sender, subject, body, atts) in enumerate(filtered[:5]):
        lines.append(f'{i+1}. {sender} — {subject}')
        if translate_enabled and not re.search(r'[\u4e00-\u9fff]', subject):
            cn = translator.translate(subject)
            if cn and cn != subject:
                lines.append(f'   🌐 {cn}')
        summary_data = summarizer.extract(body) if summary_enabled else None
        if summary_data and (summary_data.ips or summary_data.amounts):
            parts = []
            if summary_data.ips:
                parts.append('IP: ' + ' / '.join(summary_data.ips[:2]))
            if summary_data.amounts:
                parts.append(' / '.join(summary_data.amounts[:2]))
            if parts:
                lines.append(f'   📝 {" | ".join(parts)}')
        if atts:
            lines.append(f'   📎 {", ".join(a["name"] for a in atts[:3])}')
    if len(filtered) > 5:
        lines.append(f'   … and {len(filtered) - 5} more')

    notification = EmailNotification(
        account=account_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        sender=f'{len(filtered)} senders',
        subject=f'{len(filtered)} emails (merged)',
        body_preview='\n'.join(lines),
        body_full='\n'.join(lines),
        summary=summarizer.extract(''),
        attachments=[],
    )

    await on_email(notification)
