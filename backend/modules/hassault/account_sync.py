"""This node's half of hassault account data: the proxy, the import, settings sync.

The game server owns an account's settings, inventory, XP and drops
(`backend/games_server/hassault_account.py`). A node signed in to an account,
talking to a server that advertises it, is a client of that service:

- **Skin routes proxy** (`routes.py` `/skins/*`) to `/api/hassault/account/*`
  with the node's bearer token. Signed out, or against a server too old to have
  the service, they answer from the local `app.db` exactly as before, so a LAN
  or offline node still works.
- **Local history is imported once** per account and machine, the first time the
  service is reachable: the inventory and every match's XP. The server keeps the
  import apart (untradable items, pre-claimed levels); a marker row here stops
  this machine asking again, and the server's own `(account, node)` key makes a
  repeat harmless anyway.
- **Settings sync** for account-scoped keys only (`settings_schema`): pulled on
  startup and sign-in, the server's copy winning; pushed as each one changes.
  The native client needs nothing for this, since it reads the node's bag.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: Account id -> whether the server has the service, once asked.
_ready: dict[str, bool] = {}
_import_lock = threading.Lock()


def _account_keys() -> set[str]:
    from backend.modules.hassault import settings_schema

    return {spec.key for spec in settings_schema.SCHEMA if spec.scope == "account"}


def _base_and_headers() -> tuple[str, dict[str, str]]:
    from backend.modules.games import server_auth

    return server_auth._http_base(), server_auth._bearer()


async def request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any] | None:
    """One call to the account service, as this node's account. `None` when the
    server could not be reached; otherwise its status and body."""
    import httpx

    base, headers = _base_and_headers()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.request(
                method,
                f"{base}/api/hassault/account{path}",
                json=json,
                params=params,
                headers=headers,
            )
    except httpx.HTTPError:
        return None
    try:
        body = res.json()
    except ValueError:
        body = None
    return res.status_code, body


async def active_account() -> str | None:
    """The signed-in account, if the server has the service for it. Asked once
    per account per process; an unreachable server is asked again next time."""
    from backend.modules.games import server_auth

    account_id = server_auth.signed_in_account_id()
    if not account_id:
        return None
    if account_id in _ready:
        return account_id if _ready[account_id] else None
    answer = await request("GET", "/progression")
    if answer is None:
        return None
    _ready[account_id] = answer[0] == 200
    if _ready[account_id]:
        await import_once(account_id)
    return account_id if _ready[account_id] else None


# ---- import ---------------------------------------------------------------------


def _marker_conn() -> sqlite3.Connection:
    from backend.modules.database.app_db import ensure_app_db_dir

    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hassault_exports ("
        " account_id TEXT PRIMARY KEY, exported_at REAL NOT NULL)"
    )
    return conn


def _local_payload(account_id: str) -> dict[str, Any]:
    from backend.modules.hassault import results
    from backend.modules.hassault.skins import skin_manager

    items = [i.to_dict() for i in skin_manager.get_inventory(account_id)]
    results.init_results_db()
    with results.get_db_conn() as conn:
        rows = conn.execute(
            "SELECT id, xp FROM hassault_matches WHERE account_id = ?", (account_id,)
        ).fetchall()
    matches = [{"id": str(r["id"]), "xp": int(r["xp"])} for r in rows]
    return {"items": items, "matches": matches}


async def import_once(account_id: str) -> dict[str, Any] | None:
    """Send this machine's local history for `account_id`, if it never has."""
    from backend.modules.network import identity

    with _import_lock:
        conn = _marker_conn()
        try:
            done = conn.execute(
                "SELECT 1 FROM hassault_exports WHERE account_id = ?", (account_id,)
            ).fetchone()
        finally:
            conn.close()
    if done:
        return None
    payload = _local_payload(account_id)
    payload["nodeId"] = identity.load_identity().node_id
    answer = await request("POST", "/import", json=payload)
    if answer is None or answer[0] != 200:
        return None
    with _import_lock:
        conn = _marker_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO hassault_exports VALUES (?, ?)",
                (account_id, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    logger.info("hassault: imported local history for %s: %s", account_id, answer[1])
    return answer[1]


# ---- settings --------------------------------------------------------------------


async def pull_settings() -> dict[str, Any] | None:
    """Make the node's bag match the account's settings. The server's copy wins;
    an account with none yet is seeded from this machine's values."""
    from backend.modules.settings.routes import _lock, _read, _write

    account_id = await active_account()
    if account_id is None:
        return None
    answer = await request("GET", "/settings")
    if answer is None or answer[0] != 200 or not isinstance(answer[1], dict):
        return None
    keys = _account_keys()
    remote = {k: v for k, v in (answer[1].get("values") or {}).items() if k in keys}
    if not remote:
        with _lock():
            local = {k: v for k, v in _read().items() if k in keys}
        if local:
            await request("PUT", "/settings", json={"values": local})
        return local
    with _lock():
        data = _read()
        data.update(remote)
        _write(data)
    return remote


def push_setting(key: str, value: Any) -> None:
    """Send one changed account-scoped setting up, off the request's thread.

    Fire-and-forget, and skipped unless this account is known to have the
    service: a settings write must never wait on, or fail because of, a server.
    """
    from backend.modules.games import server_auth

    account_id = server_auth.signed_in_account_id()
    if not account_id or not _ready.get(account_id) or key not in _account_keys():
        return

    def _send() -> None:
        import httpx

        base, headers = _base_and_headers()
        try:
            httpx.put(
                f"{base}/api/hassault/account/settings",
                json={"values": {key: value}},
                headers=headers,
                timeout=10.0,
            )
        except httpx.HTTPError:
            logger.debug("hassault: could not push %s", key, exc_info=True)

    threading.Thread(target=_send, daemon=True).start()


def schedule_sync() -> None:
    """Probe, import and pull, in the background. Runs at startup and sign-in."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # A new sign-in may be a new account; ask again.
    _ready.clear()

    async def _run() -> None:
        try:
            await pull_settings()
        except Exception:  # noqa: BLE001 - background; the next sign-in retries
            logger.debug("hassault account sync failed", exc_info=True)

    task = loop.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_tasks: set[Any] = set()
