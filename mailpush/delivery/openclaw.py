"""OpenClaw delivery adapter."""
from __future__ import annotations

import asyncio
import time

import aiohttp

from .base import DeliveryAdapter


class OpenClawAdapter(DeliveryAdapter):
    """Send events to OpenClaw gateway, message API, or CLI fallback."""

    type = "openclaw"

    async def send(self, event, rendered_message: str) -> dict:
        mode = self.config.get("mode", "gateway_webhook")
        if mode == "gateway_webhook":
            return await self._send_gateway(event, rendered_message)
        if mode == "message_api":
            return await self._send_message_api(event, rendered_message)
        if mode == "command":
            return await self._send_command(rendered_message)
        return self.result(False, message=f'Unsupported OpenClaw mode "{mode}"')

    async def _send_gateway(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()
        url = self.config.get("url")
        token = self.config.get("token", "")
        timeout = int(self.config.get("timeout", 10))
        if not url:
            return self.result(False, message="Missing OpenClaw gateway url", started_at=started)

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "source": "mailpush",
            "event": getattr(event, "type", "email.received"),
            "session": self.config.get("session", "main"),
            "text": rendered_message,
            "data": event.model_dump(mode="json") if hasattr(event, "model_dump") else event,
        }
        return await self._post(url, payload, headers, timeout, started)

    async def _send_message_api(self, event, rendered_message: str) -> dict:
        started = time.perf_counter()
        url = self.config.get("url")
        token = self.config.get("token", "")
        timeout = int(self.config.get("timeout", 10))
        if not url:
            return self.result(False, message="Missing OpenClaw message API url", started_at=started)

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "channel": self.config.get("channel"),
            "target": self.config.get("target"),
            "text": rendered_message,
            "data": event.model_dump(mode="json") if hasattr(event, "model_dump") else event,
        }
        return await self._post(url, payload, headers, timeout, started)

    async def _send_command(self, rendered_message: str) -> dict:
        started = time.perf_counter()
        command = self.config.get("command", "openclaw")
        args = [str(a) for a in self.config.get("args", [])]
        timeout = int(self.config.get("timeout", 15))

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
                message="openclaw command failed",
                error=(stderr or stdout).decode(errors="replace").strip(),
                started_at=started,
            )
        except Exception as exc:
            return self.result(False, message="openclaw command error", error=str(exc), started_at=started)

    async def _post(self, url: str, payload: dict, headers: dict, timeout: int, started: float) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    text = await resp.text()
                    return self.result(
                        200 <= resp.status < 300,
                        status=resp.status,
                        message=text[:200] or resp.reason,
                        started_at=started,
                    )
        except Exception as exc:
            return self.result(False, message="openclaw http error", error=str(exc), started_at=started)
