"""Hermes delivery adapter."""
from __future__ import annotations

import asyncio
import os
import time

import aiohttp

from .base import DeliveryAdapter


class HermesAdapter(DeliveryAdapter):
    """Send messages through Hermes CLI or HTTP API."""

    type = "hermes"

    async def send(self, event, rendered_message: str) -> dict:
        mode = self.config.get("mode", "cli")
        if mode == "api":
            return await self._send_api(event, rendered_message)
        if mode == "cli":
            return await self._send_cli(rendered_message)
        return self.result(False, message=f'Unsupported Hermes mode "{mode}"')

    async def _send_cli(self, rendered_message: str) -> dict:
        started = time.perf_counter()
        command = self.config.get("command") or self._default_command()
        target = self.config.get("target")
        timeout = int(self.config.get("timeout", 15))
        if not target:
            return self.result(False, message="Missing Hermes target", started_at=started)

        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                "send",
                "--to",
                target,
                rendered_message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0:
                return self.result(True, status=0, message="sent", started_at=started)
            return self.result(
                False,
                status=proc.returncode or 1,
                message="hermes cli failed",
                error=(stderr or stdout).decode(errors="replace").strip(),
                started_at=started,
            )
        except Exception as exc:
            return self.result(False, message="hermes cli error", error=str(exc), started_at=started)

    async def _send_api(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()
        url = self.config.get("url")
        target = self.config.get("target")
        token = self.config.get("token", "")
        timeout = int(self.config.get("timeout", 15))
        if not url:
            return self.result(False, message="Missing Hermes API url", started_at=started)

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "target": target,
            "message": rendered_message,
            "data": event.model_dump(mode="json") if hasattr(event, "model_dump") else event,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    body = await resp.text()
                    return self.result(
                        200 <= resp.status < 300,
                        status=resp.status,
                        message=body[:200] or resp.reason,
                        started_at=started,
                    )
        except Exception as exc:
            return self.result(False, message="hermes api error", error=str(exc), started_at=started)

    @staticmethod
    def _default_command() -> str:
        local = os.path.expanduser("~/.local/bin/hermes")
        return local if os.path.exists(local) else "hermes"
