"""Backward-compatibility shim: mailpush.config → mailpush.core.config

Old code doing `from mailpush.config import load` continues to work.
"""
from mailpush.core.config import (
    load,
    save,
    _default_path,
    DEFAULT_CONFIG,
    _migrate_deliveries,
    _migrate_delivery_targets,
    _migrate_processing,
    _migrate_accounts,
    _migrate_routes,
)

__all__ = [
    "load",
    "save",
    "_default_path",
    "DEFAULT_CONFIG",
]
