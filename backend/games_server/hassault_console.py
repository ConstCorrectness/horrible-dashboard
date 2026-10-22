"""The developer console against a room this server runs.

The browser's console posts `/api/hassault/console/exec` to whichever backend it
plays on; a node answers with its full console (`modules/hassault/console.py`),
and this is the game server's answer. It is deliberately much smaller:

- **Who.** The account that opened the room (`HassaultReferee.creators`), or an
  admin named in `HASSAULT_ADMINS` (comma-separated account ids). Not the host:
  the host is whoever has been seated longest, which a stranger becomes the
  moment the creator steps out.
- **Where.** Never a ranked room. A duel is rated, and a result somebody played
  in god mode is not one the ladder can stand behind — admins included.
- **What.** A whitelist of cheat CVars applied to the simulation, so the server
  honours them (`MatchPlayer.god` is checked where damage lands). No Python, no
  macros: the node's console runs scripts on the player's own machine, and this
  is a public server.
"""

from __future__ import annotations

import os
import shlex
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.games_server import auth
from backend.games_server.hassault_rooms import referee

router = APIRouter(prefix="/api/hassault", tags=["hassault-console"])

#: Modes whose results are rated. A console there is refused outright.
RANKED_MODES = frozenset({"duel"})

#: Numeric room settings the console may change, and their bounds.
GAMEPLAY_CVARS: dict[str, tuple[float, float]] = {
    "gameplay.player_max_health": (1.0, 10000.0),
    "gameplay.damage_scale": (0.0, 100.0),
    "gameplay.nade_damage_scale": (0.0, 100.0),
    "gameplay.knife_speed_boost": (0.1, 10.0),
    "gameplay.recoil_scale": (0.0, 10.0),
    "gameplay.recoil_push": (0.0, 10.0),
}
BOOL_CVARS = ("player.god", "server.bots.freeze")
TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}


class ConsoleExecRequest(BaseModel):
    command: str
    room_id: str | None = None
    player_id: str | None = None


def admins() -> set[str]:
    raw = os.environ.get("HASSAULT_ADMINS", "")
    return {a.strip() for a in raw.split(",") if a.strip()}


def _reply(
    line: str, output: list[str], affected: dict[str, Any], value: Any
) -> dict[str, Any]:
    return {
        "ok": True,
        "command": line,
        "output": output,
        "error": None,
        "affected_cvars": affected,
        "result_data": value,
    }


def _parse(line: str) -> tuple[str, str | None]:
    """`name`, `name value`, `name = value` or `name=value`; aliases resolved."""
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    if tokens and "=" in tokens[0]:
        head, _, rest = tokens[0].partition("=")
        tokens = [head, rest, *tokens[1:]]
    elif len(tokens) >= 3 and tokens[1] == "=":
        tokens = [tokens[0], *tokens[2:]]
    if not tokens:
        raise HTTPException(status_code=400, detail="empty command")
    name = tokens[0]
    if name.startswith("play."):
        name = "player." + name[5:]
    if name == "god":
        return "player.god", tokens[1] if len(tokens) > 1 else "1"
    if len(tokens) > 2:
        raise HTTPException(status_code=400, detail=f"{name} takes one value")
    return name, tokens[1] if len(tokens) > 1 else None


def authorise(account_id: str, room: Any) -> None:
    """Raise the 403 that says why `account_id` may not use the console in `room`."""
    if room.mode.id in RANKED_MODES:
        raise HTTPException(
            status_code=403, detail="the console is disabled in ranked matches"
        )
    if account_id in admins():
        return
    if referee.creators.get(room.id) != account_id:
        raise HTTPException(
            status_code=403,
            detail="only the room's creator or an admin can use the console here",
        )


def execute(account_id: str, room: Any, line: str) -> dict[str, Any]:
    name, raw = _parse(line)
    if name == "player.god":
        mine = [
            p for p in room.humans if getattr(p.conn, "account_id", None) == account_id
        ]
        if not mine:
            raise HTTPException(
                status_code=409, detail="join the room to change your own god mode"
            )
        player = mine[0]
        if raw is None:
            return _reply(line, [f'"player.god" is "{player.god}"'], {}, player.god)
        on = _bool(name, raw)
        player.god = on
        return _reply(line, [f"player.god = {on}"], {name: on}, on)
    if name == "server.bots.freeze":
        current = bool(getattr(room, "bots_frozen", False))
        if raw is None:
            return _reply(line, [f'"{name}" is "{current}"'], {}, current)
        on = _bool(name, raw)
        room.bots_frozen = on
        return _reply(line, [f"{name} = {on}"], {name: on}, on)
    if name in GAMEPLAY_CVARS:
        field = name.split(".", 1)[1]
        current = getattr(room.settings, field)
        if raw is None:
            return _reply(line, [f'"{name}" is "{current}"'], {}, current)
        lo, hi = GAMEPLAY_CVARS[name]
        try:
            value = float(raw)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"{name} takes a number"
            ) from None
        if not lo <= value <= hi:
            raise HTTPException(
                status_code=400, detail=f"{name} must be between {lo:g} and {hi:g}"
            )
        setattr(room.settings, field, value)
        return _reply(line, [f"{name} = {value}"], {name: value}, value)
    allowed = ", ".join((*BOOL_CVARS, *GAMEPLAY_CVARS))
    raise HTTPException(
        status_code=400,
        detail=f"{name!r} is not available on the game server (try: {allowed})",
    )


def _bool(name: str, raw: str) -> bool:
    word = raw.lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise HTTPException(status_code=400, detail=f"{name} takes 0 or 1")


@router.post("/console/exec")
def console_exec(
    req: ConsoleExecRequest, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    resolved = None
    if authorization and authorization.lower().startswith("bearer "):
        resolved = auth.resolve_token(authorization[7:].strip())
    if not resolved:
        raise HTTPException(
            status_code=403, detail="sign in to use the console on the game server"
        )
    account_id = str(resolved["account_id"])
    for stale in [r for r in referee.creators if r not in referee.server.rooms]:
        del referee.creators[stale]
    room = referee.server.get(req.room_id) if req.room_id else None
    if room is None:
        raise HTTPException(status_code=404, detail="join a room on this server first")
    authorise(account_id, room)
    return execute(account_id, room, req.command.strip())
