"""Agent tools for HorribleAssault.

These let the agent run the *social* side of a game — "start a match on ac_desert
and invite Rob", "who's in the match?", "what's around me?" — which is the part
that is genuinely tedious for a human mid-session and genuinely easy for an
agent.

They deliberately stop short of playing. Driving a player means producing input
at 60 Hz, and an agent turn takes seconds; a tool that "moves you forward" would
be a bot controller wearing a tool's clothes, and bot AI is its own slice with
its own design. What is here instead is everything an agent needs to *set up* and
*talk about* a match — including reading the world around a player, which makes
it a useful spotter for someone actually playing.

The tool prefix is `hassault`, matching the module id, because the orchestrator
groups tools by name prefix.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from backend.modules.hassault import assets, fabric
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
    """Which maps this node can host, from the user's own AssaultCube install."""
    root = assets.install_root()
    if root is None:
        return {
            "error": (
                "no AssaultCube install found — game content is never bundled, so "
                "hassault.installPath must point at the user's own copy"
            )
        }
    maps = assets.list_maps()
    return {"count": len(maps), "maps": sorted(m["name"] for m in maps)}


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
        "players": [
            {
                "name": p.name,
                "team": "CLA" if p.team == 0 else "RVSF",
                "position": {
                    "x": round(p.state.x, 1),
                    "y": round(p.state.y, 1),
                    "z": round(p.state.z, 1),
                },
                "rtt_ms": round(p.rtt_ms),
                "lagging": (now - p.last_command_at) > 2.0,
                "remote": isinstance(p.conn, fabric.PeerPlayerConn),
            }
            for p in room.players.values()
        ],
        "capacity": MAX_PLAYERS,
    }


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
            "distance": round(math.hypot(other.state.x - px, other.state.y - py), 1),
            "bearing": _bearing(px, py, other.state.x, other.state.y),
        }
        for other in room.players.values()
        if other.id != player.id
    ]
    others.sort(key=lambda o: o["distance"])

    return {
        "player": player.name,
        "map": room.map_name,
        "position": {"x": round(px, 1), "y": round(py, 1), "z": round(pz, 1)},
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
            "map": {"type": "string", "description": "Map name, e.g. ac_desert."},
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
