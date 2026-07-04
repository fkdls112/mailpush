"""Base classes and helpers for delivery adapters."""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any


def resolve_value(value: Any) -> Any:
    """Resolve config values such as env:NAME recursively."""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if isinstance(value, dict):
        return {k: resolve_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v) for v in value]
    return value


class DeliveryAdapter(ABC):
    """Base class for all delivery adapters."""

    type = "base"

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = resolve_value(config or {})

    @abstractmethod
    async def send(self, event, rendered_message: str) -> dict:
        """Send an event and return a normalized result."""

    def result(
        self,
        ok: bool,
        *,
        status: int = 0,
        message: str = "",
        started_at: float | None = None,
        error: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        result = {
            "ok": ok,
            "adapter": self.name,
            "type": self.type,
            "status": status,
            "message": message or ("sent" if ok else "failed"),
            "latency_ms": round((time.perf_counter() - started_at) * 1000) if started_at else 0,
        }
        if error:
            result["error"] = error
        if extra:
            result.update(extra)
        return result
