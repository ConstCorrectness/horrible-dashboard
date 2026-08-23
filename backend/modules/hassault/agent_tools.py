"""Agent tools for HorribleAssault.

These let the agent run the *social* side of a game — "start a match on hd_atrium
and invite Rob", "who's in the match?", "what's around me?" — which is the part
that is genuinely tedious for a human mid-session and genuinely easy for an
agent.

They deliberately stop short of playing. Driving a player means producing input
at 60 Hz, and an agent turn takes seconds; a tool that "moves you forward" would
be a bot controller wearing a tool's clothes. Bots are a real feature (`bots.py`)
and the agent can *field* them — `add_bot` puts three hard ones on the other team
— but the thing producing their input is a tick-rate brain on the server, not a
tool call. What is here instead is everything an agent needs to *set up* and
*talk about* a match — including reading the world around a player, which makes
it a useful spotter for someone actually playing.

The tool prefix is `hassault`, matching the module id, because the orchestrator
groups tools by name prefix.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from backend.modules.hassault import assets, bots, fabric, weapons
from backend.modules.hassault.channel import invite_friend
from backend.modules.hassault.match import MAX_PLAYERS, match_server
from backend.modules.hassault.physics import PLAYER_RADIUS
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

# How far `describe_surroundings` probes along each compass ray. Beyond this the
# answer stops being about the room you are in.
PROBE_RANGE = 24


async def list_maps(_args: dict[str, Any]) -> dict[str, Any]:
    """Which maps this node can host: the bundled ones, plus an install's if any.

    Split by origin rather than returned as one list, so the agent can answer
    "why so few maps?" without a second call — and never reports a missing
    AssaultCube install as a reason it cannot host, which it is not.
    """
    maps = assets.list_maps()
    bundled = sorted(m["name"] for m in maps if m["source"] == "bundled")
    installed = sorted(m["name"] for m in maps if m["source"] != "bundled")
    return {
        "count": len(maps),
        "maps": bundled + installed,
        "bundled": bundled,
        "from_assaultcube_install": installed,
        "install_path": str(assets.install_root() or ""),
    }


async def list_matches(_args: dict[str, Any]) -> dict[str, Any]:
    """Matches running on this node right now."""
    return {"matches": match_server.listing(), "invites": fabric.live_invites()}


async def host_match(args: dict[str, Any]) -> dict[str, Any]:
    """Open a match on a map, ready for people to join."""
    map_name = str(args.get("map") or "").strip()
    if not map_name:
        return {"error": "which map?"}
    try:
        room = match_server.create(map_name)
    except LookupError:
        return {"error": f"no map named {map_name!r} in this install"}
    except Exception as exc:
        return {"error": str(exc)}

    result: dict[str, Any] = {
        "ok": True,
        "room": room.id,
        "map": room.map_name,
        # Said plainly because it is the next thing the human has to do, and an
        # agent that opens a room and says nothing else is unhelpful.
        "next": "open the HorribleAssault pane and Join to play in it",
    }
    who = str(args.get("invite") or "").strip()
    if who:
        result["invite"] = await invite_friend(who, room.id)
    return result


async def invite(args: dict[str, Any]) -> dict[str, Any]:
    """Invite a friend to a match hosted here."""
    who = str(args.get("who") or "").strip()
    room = str(args.get("room") or "").strip()
    if not who:
        return {"error": "who should be invited?"}
    if not room:
        rooms = match_server.listing()
        if len(rooms) != 1:
            return {
                "error": (
                    "no match to invite them to — host one first"
                    if not rooms
                    else "several matches are running; say which room"
                )
            }
        room = str(rooms[0]["id"])
    return await invite_friend(who, room)


async def match_status(args: dict[str, Any]) -> dict[str, Any]:
    """Who is in a match, on which team, and how well connected they are."""
    room_id = str(args.get("room") or "").strip()
    rooms = list(match_server.rooms.values())
    if room_id:
        room = match_server.get(room_id)
        if room is None:
            return {"error": f"no match {room_id!r}"}
    elif len(rooms) == 1:
        room = rooms[0]
    elif not rooms:
        return {"matches": [], "note": "no matches are running on this node"}
    else:
        return {"error": "several matches are running; say which room"}

    import time

    now = time.monotonic()
    return {
        "room": room.id,
        "map": room.map_name,
        "scores": {"CLA": room.scores[0], "RVSF": room.scores[1]},
        "players": [
            {
                "name": p.name,
                "team": "CLA" if p.team == 0 else "RVSF",
                "position": {
                    "x": round(p.state.x, 1),
                    "y": round(p.state.y, 1),
                    "z": round(p.state.z, 1),
                },
                "health": round(p.health) if p.alive else 0,
                "alive": p.alive,
                "weapon": weapons.weapon_at(p.weapon).name,
                "kills": p.kills,
                "deaths": p.deaths,
                "bot": p.is_bot,
                "rtt_ms": round(p.rtt_ms),
                "lagging": (not p.is_bot) and (now - p.last_command_at) > 2.0,
                "remote": isinstance(p.conn, fabric.PeerPlayerConn),
            }
            for p in room.players.values()
        ],
        "capacity": MAX_PLAYERS,
    }


def _room_for(args: dict[str, Any]):
    """The room a tool means: the one named, or the only one running.

    Returned as `(room, error)` because every caller has to say something useful
    when there are two matches and no room id — "which one?" is the answer, not a
    guess.
    """
    room_id = str(args.get("room") or "").strip()
    rooms = list(match_server.rooms.values())
    if room_id:
        room = match_server.get(room_id)
        return (room, None) if room else (None, {"error": f"no match {room_id!r}"})
    if len(rooms) == 1:
        return rooms[0], None
    if not rooms:
        return None, {"error": "no matches are running on this node — host one first"}
    return None, {"error": "several matches are running; say which room"}


async def add_bot(args: dict[str, Any]) -> dict[str, Any]:
    """Put bot players into a match."""
    room, error = _room_for(args)
    if room is None:
        return error or {"error": "no match"}
    count = args.get("count")
    count = int(count) if isinstance(count, (int, float)) else 1
    skill = str(args.get("skill") or bots.DEFAULT_SKILL).lower()
    if skill not in bots.SKILLS:
        return {"error": f"skill must be one of {', '.join(bots.SKILLS)}"}
    team = args.get("team")
    if isinstance(team, str):
        team = {"cla": 0, "rvsf": 1}.get(team.strip().lower())
    added = bots.add_bots(room, max(1, min(count, MAX_PLAYERS)), skill, team)
    if not added:
        return {"error": "that match is full"}
    return {
        "ok": True,
        "room": room.id,
        "added": [
            {"name": b.name, "team": "CLA" if b.team == 0 else "RVSF"} for b in added
        ],
        "skill": skill,
        "players": len(room.players),
    }


async def remove_bot(args: dict[str, Any]) -> dict[str, Any]:
    """Take bots back out of a match."""
    room, error = _room_for(args)
    if room is None:
        return error or {"error": "no match"}
    name = str(args.get("name") or "").strip().lower()
    if name:
        match = next(
            (p for p in room.players.values() if p.is_bot and name in p.name.lower()),
            None,
        )
        if match is None:
            return {"error": f"no bot matching {name!r} in that match"}
        room.remove(match.id)
        return {"ok": True, "removed": 1, "name": match.name}
    count = args.get("count")
    removed = room.remove_bots(int(count) if isinstance(count, (int, float)) else None)
    return {"ok": True, "room": room.id, "removed": removed}


async def describe_surroundings(args: dict[str, Any]) -> dict[str, Any]:
    """What is around a player: walls, openings, and who else is nearby.

    Exists so the agent can act as a spotter for whoever is actually playing.
    The rays are cast through the same `World` the simulation uses, so what it
    reports is what the server believes, not a guess from a map file.
    """
    room_id = str(args.get("room") or "").strip()
    name = str(args.get("player") or "").strip().lower()
    rooms = list(match_server.rooms.values())
    room = (
        match_server.get(room_id)
        if room_id
        else (rooms[0] if len(rooms) == 1 else None)
    )
    if room is None:
        return {"error": "say which match" if rooms else "no matches are running"}
    players = list(room.players.values())
    if name:
        players = [p for p in players if p.name.lower() == name]
    if not players:
        return {
            "error": f"no player named {name!r} in that match"
            if name
            else "nobody is in it"
        }
    player = players[0]

    world = room.world
    px, py, pz = player.state.x, player.state.y, player.state.z
    directions = {
        "east": (1, 0),
        "north-east": (0.7071, 0.7071),
        "north": (0, 1),
        "north-west": (-0.7071, 0.7071),
        "west": (-1, 0),
        "south-west": (-0.7071, -0.7071),
        "south": (0, -1),
        "south-east": (0.7071, -0.7071),
    }
    openings: dict[str, Any] = {}
    for label, (dx, dy) in directions.items():
        distance = PROBE_RANGE
        for stepIndex in range(1, PROBE_RANGE + 1):
            cx = int(math.floor(px + dx * stepIndex))
            cy = int(math.floor(py + dy * stepIndex))
            if (
                world.is_solid(cx, cy)
                or world.ceil_at(cx, cy) - world.floor_at(cx, cy) < 2
            ):
                distance = stepIndex - 1
                break
        openings[label] = distance

    others = [
        {
            "name": other.name,
            "team": "CLA" if other.team == 0 else "RVSF",
            "enemy": other.team != player.team,
            "alive": other.alive,
            "health": round(other.health) if other.alive else 0,
            "distance": round(math.hypot(other.state.x - px, other.state.y - py), 1),
            "bearing": _bearing(px, py, other.state.x, other.state.y),
            # Whether the shot exists, from the *server's* world — the same ray a
            # trigger pull would trace. This is what makes the tool a spotter
            # rather than a minimap: "behind the wall" is the useful half.
            "line_of_sight": _has_los(room, player, other),
        }
        for other in room.players.values()
        if other.id != player.id
    ]
    others.sort(key=lambda o: o["distance"])
    weapon = weapons.weapon_at(player.weapon)

    return {
        "player": player.name,
        "map": room.map_name,
        "position": {"x": round(px, 1), "y": round(py, 1), "z": round(pz, 1)},
        "health": round(player.health) if player.alive else 0,
        "alive": player.alive,
        "weapon": weapon.name,
        "ammo": player.ammo.get(player.weapon, 0),
        "reserve": player.reserve.get(player.weapon, 0),
        "facing": _bearing(
            0, 0, math.cos(player.state.yaw), math.sin(player.state.yaw)
        ),
        "on_ground": player.state.on_ground,
        # In cubes, capped at the probe range — "24" means "at least 24".
        "clear_distance": openings,
        "probe_range": PROBE_RANGE,
        "body_width": round(PLAYER_RADIUS * 2, 1),
        "others": others,
    }


def _has_los(room, watcher, other) -> bool:
    """Whether `watcher` could actually shoot `other` right now.

    Eye to eye through the same `raycast_world` a shot uses, so the answer cannot
    disagree with what would happen if the trigger were pulled.
    """
    eye = weapons.eye_position(watcher.state.x, watcher.state.y, watcher.state.z)
    target = weapons.eye_position(other.state.x, other.state.y, other.state.z)
    vx, vy, vz = target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]
    length = math.sqrt(vx * vx + vy * vy + vz * vz)
    if length < 1e-6:
        return True
    reach = weapons.raycast_world(
        room.world, eye, (vx / length, vy / length, vz / length), length
    )
    return reach >= length - 0.5


def _bearing(x0: float, y0: float, x1: float, y1: float) -> str:
    """A compass word for a direction. The agent talks to a human, and "north-east"
    is worth more to them than 0.7853 radians."""
    angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360
    names = [
        "east",
        "north-east",
        "north",
        "north-west",
        "west",
        "south-west",
        "south",
        "south-east",
    ]
    return names[int((angle + 22.5) % 360 // 45)]


def register_hassault_tools() -> None:
    registry.agent_tools["hassault.list_maps"] = AgentTool(
        name="hassault.list_maps",
        description="List the AssaultCube maps this node can host a match on.",
        handler=list_maps,
        group="hassault",
    )
    registry.agent_tools["hassault.list_matches"] = AgentTool(
        name="hassault.list_matches",
        description=(
            "List HorribleAssault matches running on this node, plus any match "
            "invitations received from friends."
        ),
        handler=list_matches,
        group="hassault",
    )
    registry.agent_tools["hassault.host"] = AgentTool(
        name="hassault.host",
        description=(
            "Open a HorribleAssault match on a map, optionally inviting a friend "
            "at the same time. Returns the room id."
        ),
        handler=host_match,
        group="hassault",
        parameters={
            "map": {"type": "string", "description": "Map name, e.g. hd_atrium."},
            "invite": {
                "type": "string",
                "description": "Optional friend to invite: their name or friend code.",
            },
        },
        required=["map"],
        side_effect=True,
    )
    registry.agent_tools["hassault.invite"] = AgentTool(
        name="hassault.invite",
        description=(
            "Invite a friend to a match hosted on this node. The room may be "
            "omitted when only one match is running."
        ),
        handler=invite,
        group="hassault",
        parameters={
            "who": {"type": "string", "description": "Friend's name or friend code."},
            "room": {"type": "string", "description": "Match room id."},
        },
        required=["who"],
        side_effect=True,
    )
    registry.agent_tools["hassault.status"] = AgentTool(
        name="hassault.status",
        description=(
            "Who is in a match: names, teams, positions, latency, and whether "
            "they are playing from another node."
        ),
        handler=match_status,
        group="hassault",
        parameters={"room": {"type": "string", "description": "Match room id."}},
    )
    registry.agent_tools["hassault.add_bot"] = AgentTool(
        name="hassault.add_bot",
        description=(
            "Add bot players to a HorribleAssault match. Skill is easy, normal "
            "or hard. Team defaults to balancing the sides; name a team to stack "
            "them all as opponents."
        ),
        handler=add_bot,
        group="hassault",
        parameters={
            "room": {"type": "string", "description": "Match room id."},
            "count": {"type": "integer", "description": "How many bots (default 1)."},
            "skill": {
                "type": "string",
                "enum": list(bots.SKILLS),
                "description": "Difficulty; defaults to normal.",
            },
            "team": {
                "type": "string",
                "enum": ["CLA", "RVSF"],
                "description": "Force a team instead of balancing.",
            },
        },
        side_effect=True,
    )
    registry.agent_tools["hassault.remove_bot"] = AgentTool(
        name="hassault.remove_bot",
        description=(
            "Remove bots from a match — one by name, a number of them, or all of "
            "them when neither is given."
        ),
        handler=remove_bot,
        group="hassault",
        parameters={
            "room": {"type": "string", "description": "Match room id."},
            "name": {"type": "string", "description": "A particular bot's name."},
            "count": {
                "type": "integer",
                "description": "How many to remove, newest first. Omit for all.",
            },
        },
        side_effect=True,
    )
    registry.agent_tools["hassault.surroundings"] = AgentTool(
        name="hassault.surroundings",
        description=(
            "Describe what is around a player in a match — how far the walls are "
            "in each compass direction, and who else is nearby. Use this to act "
            "as a spotter for someone playing."
        ),
        handler=describe_surroundings,
        group="hassault",
        parameters={
            "room": {"type": "string", "description": "Match room id."},
            "player": {
                "type": "string",
                "description": "Player name; defaults to the first.",
            },
        },
    )
    registry.agent_tools["hassault.console_exec"] = AgentTool(
        name="hassault.console_exec",
        description=(
            "Execute a developer console command, CVar assignment/query, or Python script "
            "in hAssault (e.g. 'draw.hitboxes 1', 'server.timescale 0.5', 'player.give carbine')."
        ),
        handler=console_exec_tool,
        group="hassault",
        parameters={
            "command": {"type": "string", "description": "Command string or Python script."},
            "room": {"type": "string", "description": "Optional match room id."},
        },
        required=["command"],
        side_effect=True,
    )
    registry.agent_tools["hassault.run_macro"] = AgentTool(
        name="hassault.run_macro",
        description="Execute a named hAssault developer console macro (e.g. 'warmup', 'bot_1v5').",
        handler=run_macro_tool,
        group="hassault",
        parameters={
            "name": {"type": "string", "description": "Macro name, e.g. warmup, bot_1v5."},
            "room": {"type": "string", "description": "Optional match room id."},
        },
        required=["name"],
        side_effect=True,
    )


async def console_exec_tool(args: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.hassault.console import ConsoleExecRequest, console_registry

    cmd = str(args.get("command") or "").strip()
    room_id = str(args.get("room") or "").strip() or None
    if not cmd:
        return {"error": "command string required"}
    req = ConsoleExecRequest(command=cmd, room_id=room_id)
    res = await console_registry.execute(req)
    return {
        "ok": res.ok,
        "command": res.command,
        "output": res.output,
        "error": res.error,
        "affected_cvars": res.affected_cvars,
    }


async def run_macro_tool(args: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.hassault.console import ConsoleExecRequest, console_registry

    name = str(args.get("name") or "").strip()
    room_id = str(args.get("room") or "").strip() or None
    if not name:
        return {"error": "macro name required"}
    macro = console_registry.macros.get(name)
    if not macro:
        return {"error": f"no macro named {name!r}"}
    req = ConsoleExecRequest(command=f"macro.run({name!r})", room_id=room_id)
    res = await console_registry.execute(req)
    return {
        "ok": res.ok,
        "macro": name,
        "output": res.output,
        "error": res.error,
        "affected_cvars": res.affected_cvars,
    }

