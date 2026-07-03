"""Configuration management — JSON/YAML file based."""
import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG = """{
  "accounts": [],
  "delivery_targets": [],
  "translate": false,
  "summary": true,
  "attachment_info": true,
  "merge_batch": true,
  "merge_interval": 30,
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


def load(path: Optional[str] = None) -> dict:
    """Load config from file. Creates default if not exists."""
    p = Path(path) if path else _default_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_CONFIG)
    with open(p) as f:
        cfg = json.load(f)
    # Ensure nested defaults
    cfg.setdefault('delivery_targets', [])
    cfg.setdefault('translate', False)
    cfg.setdefault('summary', True)
    cfg.setdefault('attachment_info', True)
    cfg.setdefault('merge_batch', True)
    cfg.setdefault('merge_interval', 30)
    cfg.setdefault('filters', {"block_senders": [], "block_keywords": [], "allow_only_senders": []})
    cfg.setdefault('smtp_reply_from', '')
    cfg.setdefault('server', {"host": "127.0.0.1", "port": 8080})
    return cfg


def save(cfg: dict, path: Optional[str] = None) -> None:
    """Save config to file with restrictive permissions."""
    p = Path(path) if path else _default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically with private permissions
    tmp = p.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(p)
