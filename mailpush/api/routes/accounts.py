"""Account routes — /api/v1/accounts."""
import asyncio
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from ...api.schemas import AccountTestResult, EmailAccount
from ... import config as config_mgr
from ... import imap

router = APIRouter(tags=["accounts"])


@router.get("/accounts")
async def list_accounts():
    """List all configured IMAP accounts with live connection status."""
    cfg = config_mgr.load()
    safe = []
    for a in cfg.get("accounts", []):
        safe.append(
            {
                "name": a["name"],
                "host": a["host"],
                "port": a.get("port", 993),
                "username": a["username"],
                "has_smtp": bool(a.get("smtp_host")),
            }
        )
    return {"accounts": safe, "status": imap.get_status()}


@router.post("/accounts")
async def add_account(account: EmailAccount):
    """Add a new IMAP account. Server restart required to activate."""
    cfg = config_mgr.load()
    names = [a["name"] for a in cfg.get("accounts", [])]
    if account.name in names:
        raise HTTPException(409, f'Account "{account.name}" already exists')
    cfg.setdefault("accounts", []).append(account.model_dump())
    config_mgr.save(cfg)
    return {
        "ok": True,
        "message": f'Account "{account.name}" added. Restart server to activate.',
    }


@router.delete("/accounts/{name}")
async def remove_account(name: str):
    """Remove an IMAP account. Server restart required to apply."""
    cfg = config_mgr.load()
    before = len(cfg.get("accounts", []))
    cfg["accounts"] = [a for a in cfg.get("accounts", []) if a["name"] != name]
    if len(cfg["accounts"]) == before:
        raise HTTPException(404, f'Account "{name}" not found')
    config_mgr.save(cfg)
    return {"ok": True, "message": f'Account "{name}" removed. Restart server to apply.'}


@router.post("/accounts/{name}/test", response_model=AccountTestResult)
async def test_account(name: str):
    """Test IMAP connectivity for a configured account.

    Attempts a real SSL login and immediately logs out.
    """
    cfg = config_mgr.load()
    acct = next((a for a in cfg.get("accounts", []) if a["name"] == name), None)
    if acct is None:
        raise HTTPException(404, f'Account "{name}" not found')

    import ssl

    import aioimaplib

    host = acct["host"]
    port = acct.get("port", 993)
    user = acct["username"]
    pwd = acct["password"]
    t0 = time.monotonic()

    try:
        client = aioimaplib.IMAP4_SSL(host, port, timeout=10)
        await asyncio.wait_for(client.wait_hello_from_server(), timeout=10)
        resp, _ = await asyncio.wait_for(client.login(user, pwd), timeout=10)
        await asyncio.wait_for(client.logout(), timeout=5)
        latency = int((time.monotonic() - t0) * 1000)

        if resp == "OK":
            return AccountTestResult(
                ok=True,
                account=name,
                message="IMAP login successful",
                latency_ms=latency,
            )
        else:
            return AccountTestResult(
                ok=False,
                account=name,
                message=f"IMAP login failed: {resp}",
                latency_ms=latency,
                error=resp,
            )
    except Exception as exc:
        latency = int((time.monotonic() - t0) * 1000)
        return AccountTestResult(
            ok=False,
            account=name,
            message="Connection failed",
            latency_ms=latency,
            error=str(exc),
        )
