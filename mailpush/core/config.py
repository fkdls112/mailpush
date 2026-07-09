"""Configuration management — JSON/YAML file based.

Config format (v2):
  accounts: list of account dicts (with optional enabled/type fields, smtp as sub-object)
  deliveries: dict[name, {type, config}]  (new format; list also accepted for compat)
  delivery_targets: list[str]             (legacy, auto-converted to deliveries dict)
  routes: list of route rules
  processing: {summary, translate, attachment_info, body_max_chars, merge_batch, merge_interval}
  filters: {block_senders, block_keywords, allow_only_senders}
  api_token: str
  server: {host, port}
"""
import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG = """{
  "accounts": [],
  "delivery_targets": [],
  "deliveries": {},
  "routes": [],
  "processing": {
    "summary": true,
    "translate": false,
    "attachment_info": true,
    "body_max_chars": 0,
    "merge_batch": true,
    "merge_interval": 30
  },
  "filters": {
    "block_senders": [],
    "block_keywords": [],
    "allow_only_senders": []
  },
  "smtp_reply_from": "",
  "api_token": "",
  "server": {
    "host": "127.0.0.1",
    "port": 8080
  }
}"""


def _default_path() -> Path:
    """Default config path: ~/.config/mailpush/config.json"""
    xdg = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    return Path(xdg) / 'mailpush' / 'config.json'


def _migrate_deliveries(cfg: dict) -> dict:
    """Migrate deliveries from list format to dict format.

    Old:  "deliveries": [{"name": "foo", "type": "hermes", "config": {...}}]
    New:  "deliveries": {"foo": {"type": "hermes", "config": {...}}}
    """
    raw = cfg.get('deliveries', {})
    if isinstance(raw, list):
        result: dict = {}
        for i, entry in enumerate(raw):
            name = entry.get('name', f"{entry.get('type', 'adapter')}-{i}")
            result[name] = {
                'type': entry.get('type', ''),
                'config': entry.get('config', {}),
            }
        cfg['deliveries'] = result
    return cfg


def _migrate_delivery_targets(cfg: dict) -> dict:
    """Convert legacy delivery_targets list into deliveries dict entries.

    Legacy targets are kept in delivery_targets for backward compat, but also
    represented in deliveries so the rest of the code can use the unified path.
    """
    targets: list = cfg.get('delivery_targets', [])
    if not targets:
        return cfg
    deliveries: dict = cfg.setdefault('deliveries', {})
    for i, target in enumerate(targets):
        legacy_name = f'hermes-legacy-{i}'
        if legacy_name not in deliveries and not any(
            v.get('type') == 'hermes' and v.get('config', {}).get('target') == target
            for v in deliveries.values()
        ):
            deliveries[legacy_name] = {
                'type': 'hermes',
                'config': {'mode': 'cli', 'target': target},
            }
    return cfg


def _migrate_processing(cfg: dict) -> dict:
    """Move top-level processing flags into the processing sub-object.

    Handles configs that still have translate/summary/etc at root level.
    """
    processing = cfg.setdefault('processing', {})
    for key in ('summary', 'translate', 'attachment_info', 'merge_batch', 'merge_interval', 'body_max_chars'):
        if key in cfg and key not in processing:
            processing[key] = cfg.pop(key)
    # Apply defaults
    processing.setdefault('summary', True)
    processing.setdefault('translate', False)
    processing.setdefault('attachment_info', True)
    processing.setdefault('body_max_chars', 0)
    processing.setdefault('merge_batch', True)
    processing.setdefault('merge_interval', 30)
    return cfg


def _migrate_accounts(cfg: dict) -> dict:
    """Add enabled/type defaults to accounts; normalise smtp as sub-object if needed."""
    for acct in cfg.get('accounts', []):
        acct.setdefault('enabled', True)
        acct.setdefault('type', 'imap')
        # If smtp fields are at top level, keep them — sub-object form is optional
        # but if a 'smtp' sub-object already exists, don't touch it
        if 'smtp' not in acct and acct.get('smtp_host'):
            acct['smtp'] = {
                'host': acct.get('smtp_host'),
                'port': acct.get('smtp_port', 587),
                'username': acct.get('smtp_username'),
                'password': acct.get('smtp_password'),
            }
    return cfg


def _migrate_routes(cfg: dict) -> dict:
    """Upgrade route entries to new format.

    Old match keys:
        account (list|str), sender_contains, subject_contains, priority, tags
    New match keys (additional):
        accounts (alias for account), keywords (alias for subject_contains),
        min_priority
    Deliveries in route can now reference adapter names from the deliveries dict.
    Old key 'adapters' is kept as alias.
    """
    for route in cfg.get('routes', []):
        match = route.get('match', {})
        # Normalise account → accounts
        if 'account' in match and 'accounts' not in match:
            val = match.pop('account')
            match['accounts'] = [val] if isinstance(val, str) else val
        # Normalise subject_contains → keywords
        if 'subject_contains' in match and 'keywords' not in match:
            val = match.pop('subject_contains')
            match['keywords'] = [val] if isinstance(val, str) else val
        route['match'] = match
        # Normalise adapters → deliveries reference list
        if 'adapters' in route and 'deliveries' not in route:
            route['deliveries'] = route['adapters']
    return cfg


def load(path: Optional[str] = None) -> dict:
    """Load config from file, applying all migrations. Creates default if not exists."""
    p = Path(path) if path else _default_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_CONFIG)
    with open(p) as f:
        cfg = json.load(f)

    # Structural defaults
    cfg.setdefault('delivery_targets', [])
    cfg.setdefault('deliveries', {})
    cfg.setdefault('routes', [])
    cfg.setdefault('filters', {
        'block_senders': [], 'block_keywords': [], 'allow_only_senders': []
    })
    cfg.setdefault('smtp_reply_from', '')
    cfg.setdefault('api_token', '')
    cfg.setdefault('server', {'host': '127.0.0.1', 'port': 8080})
    cfg.setdefault('accounts', [])

    # Run migrations
    cfg = _migrate_deliveries(cfg)
    cfg = _migrate_delivery_targets(cfg)
    cfg = _migrate_processing(cfg)
    cfg = _migrate_accounts(cfg)
    cfg = _migrate_routes(cfg)

    return cfg


def save(cfg: dict, path: Optional[str] = None) -> None:
    """Save config to file with restrictive permissions."""
    p = Path(path) if path else _default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(p)


# ── Back-compat shim — old code does `from mailpush.config import load` ──────

def _default_path_compat() -> Path:
    return _default_path()
