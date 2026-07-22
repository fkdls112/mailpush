"""Proxy-aware IMAP4_SSL client for MailPush.

Extends aioimaplib.IMAP4_SSL so a single account can route its IMAP
connection through an HTTP CONNECT or SOCKS5 proxy, independent of the
container-level HTTP(S)_PROXY environment variables.

Design
------
aioimaplib.IMAP4.create_client() calls:

    loop.create_connection(lambda: self.protocol, host, port, ssl=ssl_context)

To inject a proxy we instead:
  1. Use python-socks' async proxy to open a raw TCP socket that is
     already tunnelled to the target IMAP host:port through the proxy.
  2. Hand that connected socket to loop.create_connection(..., sock=sock,
     ssl=ssl_context, server_hostname=host) so TLS is negotiated on top.

If no proxy is configured we fall back to the stock behaviour.
"""
import asyncio
import ssl
from typing import Callable, Optional

import aioimaplib

try:
    from python_socks import ProxyType
    from python_socks.async_.asyncio import Proxy
    _HAS_SOCKS = True
except Exception:  # pragma: no cover - import guard
    _HAS_SOCKS = False


def _normalize_proxy(proxy: Optional[dict]) -> Optional[dict]:
    """Validate/normalize a proxy config dict. Returns None if disabled/invalid."""
    if not proxy or not isinstance(proxy, dict):
        return None
    if not proxy.get('enabled'):
        return None
    host = (proxy.get('host') or '').strip()
    port = proxy.get('port')
    if not host or not port:
        return None
    ptype = (proxy.get('type') or 'http').strip().lower()
    if ptype not in ('http', 'socks5'):
        ptype = 'http'
    return {
        'type': ptype,
        'host': host,
        'port': int(port),
        'username': (proxy.get('username') or '').strip() or None,
        'password': (proxy.get('password') or '').strip() or None,
    }


class ProxyIMAP4SSL(aioimaplib.IMAP4_SSL):
    """IMAP4_SSL that tunnels through an HTTP/SOCKS5 proxy when configured."""

    def __init__(self, host: str, port: int = 993,
                 loop: asyncio.AbstractEventLoop = None,
                 timeout: float = aioimaplib.IMAP4.TIMEOUT_SECONDS,
                 ssl_context: ssl.SSLContext = None,
                 proxy: Optional[dict] = None):
        self._proxy_cfg = _normalize_proxy(proxy)
        super().__init__(host, port, loop, timeout, ssl_context)

    def create_client(self, host: str, port: int,
                      loop: asyncio.AbstractEventLoop,
                      conn_lost_cb: Callable = None,
                      ssl_context: ssl.SSLContext = None) -> None:
        # No proxy → stock behaviour (also handles SSL context default).
        if not self._proxy_cfg:
            return super().create_client(host, port, loop, conn_lost_cb, ssl_context)

        if not _HAS_SOCKS:
            raise RuntimeError(
                'python-socks not installed; per-account proxy unavailable')

        if ssl_context is None:
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

        local_loop = loop if loop is not None else asyncio.get_event_loop()
        self.protocol = aioimaplib.IMAP4ClientProtocol(local_loop, conn_lost_cb)
        self._client_task = local_loop.create_task(
            self._connect_via_proxy(local_loop, host, port, ssl_context)
        )

    async def _connect_via_proxy(self, loop, host, port, ssl_context):
        cfg = self._proxy_cfg
        ptype = ProxyType.SOCKS5 if cfg['type'] == 'socks5' else ProxyType.HTTP
        proxy = Proxy(
            proxy_type=ptype,
            host=cfg['host'],
            port=cfg['port'],
            username=cfg['username'],
            password=cfg['password'],
        )
        # Open a raw TCP socket already tunnelled to host:port.
        sock = await proxy.connect(dest_host=host, dest_port=port, timeout=self.timeout)
        # Wrap with TLS and bind the aioimaplib protocol.
        return await loop.create_connection(
            lambda: self.protocol,
            sock=sock,
            ssl=ssl_context,
            server_hostname=host,
        )
