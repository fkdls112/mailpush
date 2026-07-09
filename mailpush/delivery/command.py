"""Generic command delivery adapter."""
from __future__ import annotations

import asyncio
import time

from .base import DeliveryAdapter


class CommandAdapter(DeliveryAdapter):
    """Run a local command and pass the rendered message as the final argument."""

    type = "command"

    async def send(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()
        command = self.config.get("command")
        args = [str(a) for a in self.config.get("args", [])]
        timeout = int(self.config.get("timeout", 15))
        if not command:
            return self.result(False, message="Missing command", started_at=started)

        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                rendered_message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0:
                return self.result(True, status=0, message=(stdout.decode(errors="replace").strip() or "sent"), started_at=started)
            return self.result(
                False,
                status=proc.returncode or 1,
                message="command failed",
                error=(stderr or stdout).decode(errors="replace").strip(),
                started_at=started,
            )
        except Exception as exc:
            return self.result(False, message="command error", error=str(exc), started_at=started)
