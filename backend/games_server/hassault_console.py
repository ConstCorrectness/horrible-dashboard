"""The developer console against a room this server runs.

The browser's console posts `/api/hassault/console/exec` to whichever backend it
plays on; a node answers with its full console (`modules/hassault/console.py`),
and this is the game server's answer. It is deliberately much smaller.

- **Who.** Three roles, each a superset of the one before:

  - the room's **host** — its longest-seated human (`HassaultReferee.host_of`) —
    runs the room: bots in, bots out, bots frozen. The same person who already
    decides the room's practice aids, and for the same reason: somebody has to
    be able to, and the host is never nobody.
  - the room's **creator** (`HassaultReferee.creators`) also gets the cheats:
    god mode and the gameplay scales. Not the host: the host is whoever has
    been seated longest, which a stranger becomes the moment the creator steps
    out, and a stranger setting everyone's damage to 100 is griefing, not
    hosting. The creator keeps the host's powers too, seated first or not.
  - an **admin** named in `HASSAULT_ADMINS` may do all of it in any room, and
    kick players. Entries are account ids or handles (`constcorrectness`,
    `@constcorrectness`): a handle on this server is unique and can never be
    changed once claimed (`store.set_handle`), so it names one account as
    surely as the id does and is the one a person can actually read.

- **Where.** Never a ranked room. A duel is rated, and a result somebody played
  in god mode — or against a bot somebody added — is not one the ladder can
  stand behind. Admins included.
- **What.** A fixed table of commands applied to the simulation, so the server
  honours them (`MatchPlayer.god` is checked where damage lands). No Python, no
  macros: the node's console runs scripts on the player's own machine, and this
  is a public server. A call written the way the node's console takes it —
  `server.bots.add(count=3, skill="hard")` — is *parsed* (`ast`, literals only),
  never evaluated.
"""

from __future__ import annotations

import ast
import os
import shlex
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.games_server import auth, store
from backend.games_server.hassault_rooms import referee
from backend.modules.hassault import bots
from backend.modules.hassault.match import MAX_PLAYERS, RoomSettings

router = APIRouter(prefix="/api/hassault", tags=["hassault-console"])

#: Modes whose results are rated. A console there is refused outright.
RANKED_MODES = frozenset({"duel"})

#: Who may do what, lowest first. A role may do everything the ones before it may.
ROLES = ("host", "creator", "admin")

#: Numeric room settings the console may change, and their bounds.
GAMEPLAY_CVARS: dict[str, tuple[float, float]] = {
    "gameplay.player_max_health": (1.0, 10000.0),
    "gameplay.damage_scale": (0.0, 100.0),
    "gameplay.nade_damage_scale": (0.0, 100.0),
    "gameplay.knife_speed_boost": (0.1, 10.0),
    "gameplay.recoil_scale": (0.0, 10.0),
    "gameplay.recoil_push": (0.0, 10.0),
}
TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}
TEAMS = {"cla": 0, "rvsf": 1, "0": 0, "1": 1}


class ConsoleExecRequest(BaseModel):
    command: str
    room_id: str | None = None
    player_id: str | None = None


def admins() -> set[str]:
    raw = os.environ.get("HASSAULT_ADMINS", "")
    return {a.strip() for a in raw.split(",") if a.strip()}


def is_admin(account_id: str) -> bool:
    """Whether `account_id` is named in `HASSAULT_ADMINS`, by id or by handle."""
    named = admins()
    if not named:
        return False
    if account_id in named:
        return True
    handle = (store.get_account(account_id) or {}).get("handle")
    if not handle:
        return False
    return str(handle).lower() in {n.lstrip("@").lower() for n in named}


def role_of(account_id: str, room: Any) -> str | None:
    """The highest role `account_id` holds in `room` (`ROLES`), or None."""
    if is_admin(account_id):
        return "admin"
    if referee.creators.get(room.id) == account_id:
        return "creator"
    host = referee.host_of(room)
    if host and any(
        p.id == host and getattr(p.conn, "account_id", None) == account_id
        for p in room.humans
    ):
        return "host"
    return None


def _at_least(role: str | None, needed: str) -> bool:
    return role is not None and ROLES.index(role) >= ROLES.index(needed)


#: What each role is refused with, worded for the player who asked.
WHO_MAY = {
    "host": "only the room's host, its creator or an admin",
    "creator": "only the room's creator or an admin",
    "admin": "only an admin",
}


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


# -- parsing --------------------------------------------------------------------


@dataclass
class Parsed:
    name: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)

    def value(self) -> Any:
        """A cvar's one value: `name 1`, `name = 1`, `name(1)`, or None for a query."""
        if len(self.args) > 1 or self.kwargs:
            raise HTTPException(status_code=400, detail=f"{self.name} takes one value")
        return self.args[0] if self.args else None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _parse_call(line: str) -> Parsed | None:
    """`a.b.c(1, x="y")` as a name and literal arguments, or None if it isn't one.

    `ast.literal_eval` on each argument, never `eval` on the line: anything that
    is not a plain constant is refused rather than run.
    """
    if "(" not in line:
        return None
    try:
        tree = ast.parse(line, mode="eval")
    except SyntaxError:
        return None
    call = tree.body
    if not isinstance(call, ast.Call):
        return None
    name = _dotted(call.func)
    if name is None:
        return None
    try:
        args = [ast.literal_eval(a) for a in call.args]
        kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg}
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"{name} takes plain values, not expressions"
        ) from None
    return Parsed(name=name, args=args, kwargs=kwargs)


def _parse(line: str) -> Parsed:
    """A call, or `name`, `name value…`, `name = value`, `name=value`, `k=v` args."""
    call = _parse_call(line)
    if call is None:
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
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        for token in tokens[1:]:
            key, sep, rest = token.partition("=")
            if sep and key.isidentifier():
                kwargs[key] = rest
            else:
                args.append(token)
        call = Parsed(name=tokens[0], args=args, kwargs=kwargs)
    if call.name.startswith("play."):
        call.name = "player." + call.name[5:]
    if call.name == "god":
        call.name = "player.god"
        if not call.args:
            call.args = ["1"]
    return call


def _arg(call: Parsed, index: int, key: str) -> Any:
    if key in call.kwargs:
        return call.kwargs[key]
    return call.args[index] if index < len(call.args) else None


def _bool(name: str, raw: Any) -> bool:
    word = str(raw).lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise HTTPException(status_code=400, detail=f"{name} takes 0 or 1")


def _count(raw: Any, default: int = 1) -> int:
    if raw is None:
        return default
    try:
        return max(1, min(int(raw), MAX_PLAYERS))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="count takes a number") from None


# -- commands -------------------------------------------------------------------

Handler = Callable[[str, Any, Parsed, str], Awaitable[dict[str, Any]]]


async def _bots_add(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    count = _count(_arg(call, 0, "count"))
    skill = str(_arg(call, 1, "skill") or bots.DEFAULT_SKILL).lower()
    if skill not in bots.SKILLS:
        raise HTTPException(
            status_code=400, detail=f"skill is one of {', '.join(bots.SKILLS)}"
        )
    raw_team = _arg(call, 2, "team")
    team = None
    if raw_team is not None:
        team = TEAMS.get(str(raw_team).strip().lower())
        if team is None:
            raise HTTPException(status_code=400, detail="team is CLA or RVSF")
    names = await referee.add_bots(room, count, skill, team)
    if not names:
        return _reply(line, [f"[server] The room is full ({MAX_PLAYERS})."], {}, [])
    return _reply(
        line,
        [f"[server] Spawned {len(names)} bots ({skill}): {', '.join(names)}"],
        {},
        {"added": names, "skill": skill, "total": len(room.players)},
    )


async def _bots_remove(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    # Positionally, a number is how many and anything else is whose name — the
    # node takes `(name, count)`, and `server.bots.remove 2` meaning a bot
    # called "2" is not what anybody typing it meant.
    name = call.kwargs.get("name")
    count = call.kwargs.get("count")
    for raw in call.args:
        if isinstance(raw, int) or str(raw).isdigit():
            count = raw
        else:
            name = raw
    if name:
        removed = await referee.remove_bots(room, name=str(name))
        if not removed:
            return _reply(line, [f"[server] No bot matching {name!r}."], {}, [])
    else:
        removed = await referee.remove_bots(room, count=_count(count))
    return _reply(line, [f"[server] Removed {len(removed)} bots."], {}, removed)


async def _bots_kick_all(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    removed = await referee.remove_bots(room)
    return _reply(line, [f"[server] Kicked all bots ({len(removed)})."], {}, removed)


async def _bots_freeze(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    name, raw = call.name, call.value()
    current = bool(getattr(room, "bots_frozen", False))
    if raw is None:
        return _reply(line, [f'"{name}" is "{current}"'], {}, current)
    on = _bool(name, raw)
    room.bots_frozen = on
    return _reply(line, [f"{name} = {on}"], {name: on}, on)


async def _god(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    name, raw = call.name, call.value()
    mine = [p for p in room.humans if getattr(p.conn, "account_id", None) == account_id]
    if not mine:
        raise HTTPException(
            status_code=409, detail="join the room to change your own god mode"
        )
    player = mine[0]
    if raw is None:
        return _reply(line, [f'"{name}" is "{player.god}"'], {}, player.god)
    on = _bool(name, raw)
    player.god = on
    return _reply(line, [f"{name} = {on}"], {name: on}, on)


async def _gameplay(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    name, raw = call.name, call.value()
    field = name.split(".", 1)[1]
    current = getattr(room.settings, field)
    if raw is None:
        return _reply(line, [f'"{name}" is "{current}"'], {}, current)
    lo, hi = GAMEPLAY_CVARS[name]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} takes a number") from None
    if not lo <= value <= hi:
        raise HTTPException(
            status_code=400, detail=f"{name} must be between {lo:g} and {hi:g}"
        )
    setattr(room.settings, field, value)
    return _reply(line, [f"{name} = {value}"], {name: value}, value)


async def _kick(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    who = str(_arg(call, 0, "name") or "").strip().lower()
    if not who:
        raise HTTPException(status_code=400, detail="server.kick takes a player name")
    humans = room.humans
    # An exact name first, so kicking "rob" never takes "robert" instead.
    target = next((p for p in humans if p.name.lower() == who), None) or next(
        (p for p in humans if who in p.name.lower()), None
    )
    if target is None:
        raise HTTPException(status_code=404, detail=f"no player matching {who!r}")
    # The AFK kick: out of the room with their result recorded, socket left
    # open so they are told why and may come back.
    await referee._kick(target.conn)
    return _reply(line, [f"[server] Kicked {target.name}."], {}, target.name)


async def _help(account_id: str, room: Any, call: Parsed, line: str) -> dict:
    role = role_of(account_id, room)
    who = "an admin" if role == "admin" else f"this room's {role}"
    out = [f"=== game server console — you are {who} ==="]
    for name, (needed, _, usage, about) in COMMANDS.items():
        if _at_least(role, needed):
            out.append(f"  {usage:40} {about}")
    if _at_least(role, "creator"):
        for name, (lo, hi) in GAMEPLAY_CVARS.items():
            out.append(f"  {f'{name} <{lo:g}-{hi:g}>':40} room tuning")
    return _reply(line, out, {}, {"role": role})


#: Command → (the least role that may run it, its handler, how to call it, what
#: it does). Served as the console's definitions, so autocomplete offers exactly
#: what this table accepts.
COMMANDS: dict[str, tuple[str, Handler, str, str]] = {
    "help": ("host", _help, "help", "What you may run in this room"),
    "server.bots.add": (
        "host",
        _bots_add,
        "server.bots.add(count=1, skill='normal', team=None)",
        "Add bots to the room",
    ),
    "server.bots.remove": (
        "host",
        _bots_remove,
        "server.bots.remove(count=1) / (name='...')",
        "Remove the newest bots, or one by name",
    ),
    "server.bots.kick_all": (
        "host",
        _bots_kick_all,
        "server.bots.kick_all()",
        "Remove every bot",
    ),
    "server.bots.freeze": (
        "host",
        _bots_freeze,
        "server.bots.freeze <0|1>",
        "Bots stand still and hold fire",
    ),
    "player.god": ("creator", _god, "player.god <0|1>", "Your own god mode"),
    "server.kick": (
        "admin",
        _kick,
        "server.kick(name='...')",
        "Take a player out of the room (they may rejoin)",
    ),
}


async def execute(account_id: str, room: Any, line: str) -> dict[str, Any]:
    call = _parse(line)
    if call.name in COMMANDS:
        needed, handler, _, _ = COMMANDS[call.name]
    elif call.name in GAMEPLAY_CVARS:
        needed, handler = "creator", _gameplay
    else:
        allowed = ", ".join((*COMMANDS, *GAMEPLAY_CVARS))
        raise HTTPException(
            status_code=400,
            detail=f"{call.name!r} is not available on the game server (try: {allowed})",
        )
    if not _at_least(role_of(account_id, room), needed):
        raise HTTPException(
            status_code=403, detail=f"{WHO_MAY[needed]} can use {call.name} here"
        )
    return await handler(account_id, room, call, line)


async def socket_bots(conn: Any, event: str, data: dict[str, Any]) -> str | None:
    """`add_bot` / `remove_bot` from a seated socket; why it was refused, or None.

    The console's rule, checked against the socket's own account rather than a
    bearer token — so a guest who is hosting may add bots from the menu, which
    the HTTP console (sign-in only) could never let them do.
    """
    entry = referee.server.player_for(conn)
    if entry is None:
        return "join a room first"
    room = entry[0]
    if room.mode.id in RANKED_MODES:
        return "bots are not allowed in ranked matches"
    if not _at_least(role_of(conn.account_id, room), "host"):
        return f"{WHO_MAY['host']} can add or remove bots"
    try:
        count = _count(data.get("count"))
    except HTTPException:
        count = 1
    if event == "add_bot":
        skill = str(data.get("skill") or bots.DEFAULT_SKILL).lower()
        await referee.add_bots(
            room, count, skill if skill in bots.SKILLS else bots.DEFAULT_SKILL
        )
    else:
        await referee.remove_bots(room, count=count)
    return None


def authorise(account_id: str, room: Any) -> str:
    """The role `account_id` holds in `room`, or the 403 that says why it has none."""
    if room.mode.id in RANKED_MODES:
        raise HTTPException(
            status_code=403, detail="the console is disabled in ranked matches"
        )
    role = role_of(account_id, room)
    if role is None:
        raise HTTPException(
            status_code=403,
            detail=f"{WHO_MAY['host']} can use the console here",
        )
    return role


# Async, so it runs on the event loop the rooms tick on: as a plain `def` it ran
# on a worker thread, changing a room in the middle of that room's tick.
@router.post("/console/exec")
async def console_exec(
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
    return await execute(account_id, room, req.command.strip())


#: What a fresh room's settings are, for the definitions' defaults.
DEFAULTS = RoomSettings()


@router.get("/console/definitions")
def console_definitions() -> dict[str, Any]:
    """The commands and cvars this console takes, in the node's definitions shape.

    The browser's console asks whichever backend it plays on; without this it
    knew no commands here at all. Public — names and bounds, nothing about any
    room — and not filtered by role: `help` answers what *you* may run.
    """
    commands = [
        {
            "name": name,
            "namespace": name.split(".", 1)[0] if "." in name else "system",
            "description": f"{about} ({WHO_MAY[needed]})",
            "signature": usage,
            "parameters": [],
            "flags": ["server"],
            "example": usage,
        }
        for name, (needed, _, usage, about) in COMMANDS.items()
        if name not in ("player.god", "server.bots.freeze")
    ]
    cvars = [
        {
            "name": name,
            "namespace": "gameplay",
            "type": "number",
            "default_value": getattr(DEFAULTS, name.split(".", 1)[1]),
            "current_value": getattr(DEFAULTS, name.split(".", 1)[1]),
            "min_value": lo,
            "max_value": hi,
            "description": f"Room tuning ({WHO_MAY['creator']})",
            "flags": ["server"],
        }
        for name, (lo, hi) in GAMEPLAY_CVARS.items()
    ]
    return {"cvars": cvars, "commands": commands, "macros": []}
