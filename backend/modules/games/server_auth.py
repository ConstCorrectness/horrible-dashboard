"""Node-side sign-in to the central game server.

The node holds the JWT the game server issues after a GitHub sign-in, persisted
server-side (`.data/games_token.json`) and **never returned to the browser** — the
clubhouse/`google_auth.py` token pattern. The node presents it on `/game-ws`; without
it, play falls back to the dev token.

The GitHub device flow itself runs on the game server (it has the client id); the node
just proxies start/poll so the browser talks to one origin (no CORS), and captures the
issued token when it arrives.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.modules.games.client import DEFAULT_SERVER_URL
from backend.modules.settings.routes import get_value


def _token_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "games_token.json"


def _read() -> dict[str, Any] | None:
    path = _token_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def get_token() -> str | None:
    data = _read()
    return str(data["token"]) if data and data.get("token") else None


def signed_in_name() -> str | None:
    data = _read()
    if not data:
        return None
    account = data.get("account") or {}
    return account.get("display_name")


def sign_out() -> None:
    _token_path().unlink(missing_ok=True)


def _http_base() -> str:
    """The game server's HTTP base, derived from the ws:// setting."""
    url = str(get_value("games.serverUrl", DEFAULT_SERVER_URL) or DEFAULT_SERVER_URL)
    return url.replace("wss://", "https://").replace("ws://", "http://")


def _unreachable_error() -> dict[str, str]:
    """The `{error}` shape the browser already understands (see signInWithGitHub),
    for when the central game server isn't running — a friendly message beats a 500."""
    return {
        "error": (
            f"game server unreachable at {_http_base()} — start it with "
            "`uv run uvicorn backend.games_server.app:app --port 9200`"
        )
    }


async def github_start() -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{_http_base()}/auth/github/start")
            res.raise_for_status()
            return res.json()
    except httpx.HTTPError:
        return _unreachable_error()


async def github_poll(device_code: str) -> dict[str, Any]:
    """Proxy one poll to the server. On success, persist the token server-side and
    return only the account (never the raw token) to the browser."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/auth/github/poll", json={"device_code": device_code}
            )
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPError:
        return _unreachable_error()
    if data.get("token"):
        path = _token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return {"signed_in": True, "account": data.get("account")}
    return data  # {pending: true} or {error: ...}


async def leaderboard(game_id: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_http_base()}/leaderboard", params={"game_id": game_id}
        )
        res.raise_for_status()
        return res.json()


async def challenge_leaderboard(game_id: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_http_base()}/challenges/leaderboard", params={"game_id": game_id}
        )
        res.raise_for_status()
        return res.json()
