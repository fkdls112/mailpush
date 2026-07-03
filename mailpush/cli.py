"""MailPush CLI — entry point for 'mailpush' command."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import uvicorn

from . import config as config_mgr


def main():
    parser = argparse.ArgumentParser(
        prog='mailpush',
        description='Real-time email push — IMAP IDLE + REST API',
    )
    sub = parser.add_subparsers(dest='command')

    # ── serve ────────────────────────────────────────
    serve = sub.add_parser('serve', help='Start daemon + API server')
    serve.add_argument('--host', default=None, help='Bind host (default from config)')
    serve.add_argument('--port', type=int, default=None, help='Bind port (default from config)')
    serve.add_argument('--reload', action='store_true', help='Enable auto-reload (dev mode)')

    # ── status ───────────────────────────────────────
    sub.add_parser('status', help='Show account connection status')

    # ── config ───────────────────────────────────────
    config_parser = sub.add_parser('config', help='Manage configuration')
    config_sub = config_parser.add_subparsers(dest='config_action')
    config_sub.add_parser('show', help='Show current config')
    config_init = config_sub.add_parser('init', help='Create default config file')
    config_init.add_argument('--path', help='Custom config path')

    # ── accounts ─────────────────────────────────────
    acct = sub.add_parser('accounts', help='Manage email accounts')
    acct_sub = acct.add_subparsers(dest='acct_action')
    acct_sub.add_parser('list', help='List configured accounts')
    add = acct_sub.add_parser('add', help='Add an email account')
    add.add_argument('--name', required=True, help='Account name')
    add.add_argument('--host', required=True, help='IMAP host')
    add.add_argument('--port', type=int, default=993, help='IMAP port')
    add.add_argument('--user', required=True, help='IMAP username')
    add.add_argument('--pass', dest='password', required=True, help='IMAP password')
    add.add_argument('--smtp-host', help='SMTP host (optional, for reply)')
    add.add_argument('--smtp-port', type=int, default=587, help='SMTP port')
    add.add_argument('--smtp-user', help='SMTP username')
    add.add_argument('--smtp-pass', help='SMTP password')
    rm = acct_sub.add_parser('remove', help='Remove an email account')
    rm.add_argument('--name', required=True, help='Account name to remove')

    # ── test ─────────────────────────────────────────
    test = sub.add_parser('test', help='Test IMAP connection')
    test.add_argument('account', help='Account name to test')

    # ── webhook ──────────────────────────────────────
    webh = sub.add_parser('webhook', help='Manage webhooks')
    webh_sub = webh.add_subparsers(dest='wh_action')
    wh_add = webh_sub.add_parser('add', help='Register webhook')
    wh_add.add_argument('--url', required=True, help='Webhook URL')
    wh_add.add_argument('--secret', help='HMAC secret')
    webh_sub.add_parser('list', help='List webhooks')
    wh_rm = webh_sub.add_parser('remove', help='Remove webhook')
    wh_rm.add_argument('--id', required=True, dest='wid', help='Webhook ID')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == 'serve':
        cfg = config_mgr.load()
        server_cfg = cfg.get('server', {})
        host = args.host or server_cfg.get('host', '127.0.0.1')
        port = args.port or server_cfg.get('port', 8080)
        uvicorn.run(
            'mailpush.server:app',
            host=host,
            port=port,
            reload=args.reload,
            log_level='info',
        )

    elif args.command == 'status':
        cfg = config_mgr.load()
        print('Configured accounts:')
        for a in cfg.get('accounts', []):
            print(f'  {a["name"]} — {a["host"]}:{a.get("port", 993)} ({a["username"]})')
        print()
        print('Server must be running for live status. Call GET /api/health.')

    elif args.command == 'config':
        if args.config_action == 'init':
            cfg_path = args.path
            cfg = config_mgr.load(cfg_path)
            print(f'Config created at: {cfg_path or config_mgr._default_path()}')

        elif args.config_action == 'show':
            cfg = config_mgr.load()
            # Redact passwords
            safe = json.loads(json.dumps(cfg))
            for a in safe.get('accounts', []):
                a['password'] = '***'
                a['smtp_password'] = '***'
            print(json.dumps(safe, indent=2, ensure_ascii=False))

    elif args.command == 'accounts':
        if args.acct_action == 'list':
            cfg = config_mgr.load()
            if not cfg.get('accounts'):
                print('No accounts configured.')
            for a in cfg.get('accounts', []):
                smtp = f', SMTP: {a.get("smtp_host", "N/A")}' if a.get('smtp_host') else ''
                print(f'  {a["name"]} — {a["host"]}:{a.get("port", 993)} ({a["username"]}){smtp}')

        elif args.acct_action == 'add':
            cfg = config_mgr.load()
            cfg.setdefault('accounts', []).append({
                'name': args.name, 'host': args.host, 'port': args.port,
                'username': args.user, 'password': args.password,
                'smtp_host': args.smtp_host, 'smtp_port': args.smtp_port,
                'smtp_username': args.smtp_user or args.user,
                'smtp_password': args.smtp_pass or args.password,
            })
            config_mgr.save(cfg)
            print(f'Account "{args.name}" added.')

        elif args.acct_action == 'remove':
            cfg = config_mgr.load()
            before = len(cfg.get('accounts', []))
            cfg['accounts'] = [a for a in cfg.get('accounts', []) if a['name'] != args.name]
            config_mgr.save(cfg)
            if len(cfg['accounts']) < before:
                print(f'Account "{args.name}" removed.')
            else:
                print(f'Account "{args.name}" not found.')

    elif args.command == 'test':
        cfg = config_mgr.load()
        acct = next((a for a in cfg.get('accounts', []) if a['name'] == args.account), None)
        if not acct:
            print(f'Account "{args.account}" not found.')
            return
        asyncio.run(_test_connection(acct))

    elif args.command == 'webhook':
        if args.wh_action == 'add':
            from . import webhook as wh
            entry = wh.register(args.url, args.secret)
            print(f'Webhook registered: {entry.id} → {args.url}')
        elif args.wh_action == 'list':
            from . import webhook as wh
            hooks = wh.list_all()
            if not hooks:
                print('No webhooks registered.')
            for h in hooks:
                print(f'  {h.id} → {h.url} (created: {h.created_at})')
        elif args.wh_action == 'remove':
            from . import webhook as wh
            if wh.unregister(args.wid):
                print(f'Webhook "{args.wid}" removed.')
            else:
                print(f'Webhook "{args.wid}" not found.')


async def _test_connection(acct: dict):
    import aioimaplib
    print(f'Testing {acct["name"]} — {acct["host"]}:{acct.get("port", 993)} ...')
    try:
        imap = aioimaplib.IMAP4_SSL(acct['host'], acct.get('port', 993), timeout=15)
        await asyncio.wait_for(imap.wait_hello_from_server(), 15)
        await asyncio.wait_for(imap.login(acct['username'], acct['password']), 15)
        await asyncio.wait_for(imap.select('INBOX'), 15)
        result, data = await asyncio.wait_for(imap.uid_search('ALL'), 15)
        count = len(data[0].split()) if data and data[0] else 0
        print(f'  ✅ Connected! {count} emails in INBOX.')
    except Exception as e:
        print(f'  ❌ Failed: {e}')


if __name__ == '__main__':
    main()
