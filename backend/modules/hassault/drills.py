"""Aim drills for the training range: what the targets do, and how a run scores.

The range (`training.ts`, `training.rs`) runs offline and owns its targets, so a
drill cannot be a server room. What *is* served is everything a second copy
would get wrong:

- **The schedule.** Each target is given a list of input segments,
  `[tick, strafe, crouch, jump, visible]`, at a fixed `TICK_RATE`. A client plays
  them through **its own port of `physics.step`** against the map's own world, one
  fixed tick at a time, so a target accelerates, brakes and counter-strafes
  exactly like a player does, because it *is* the movement code. The decisions
  that need randomness or position (when to turn back at a rail's end, when a
  pop-up shows) are made here, by simulating the same steps, so neither client
  ports an RNG or a turn-back rule. `drill-vectors.json` pins that replaying a
  schedule lands every client on the positions this module saw.
- **The scoring.** `summarise` is the reference for the results card: accuracy,
  headshot share, time to kill, reaction time, kills per minute. Both clients port
  it and `training-stats-vectors.json` binds them to it.

A drill is either on `hd_aim`, whose stations are declared in that map's own
source under a top-level `drills` key, or on any map, where the targets stand on
the spawn points nearest the player (`spawn_strafe`).

Nothing here touches XP, credits or a rating, and nothing ever may: a drill is
scored by the client that ran it, so its numbers are a personal record only.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import dataclass
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from backend.modules.hassault import mapsource, physics

#: Fixed simulation rate for targets. A client advances targets in whole ticks of
#: `1 / TICK_RATE` regardless of its frame rate, which is what makes a schedule
#: replay to the same positions everywhere.
TICK_RATE = 60
TICK = 1.0 / TICK_RATE

#: At most this many targets, the range's own `MAX_TARGETS`.
MAX_TARGETS = 6

#: Rail half-width for targets standing on spawn points, which have no rail of
#: their own. Wide enough to strafe, narrow enough to stay clear of the walls a
#: spawn usually has nearby.
SPAWN_POST_HALF = 4.0

#: How far before the end of its rail a target turns back. It is still moving
#: when it reverses, so turning at the very end would carry it past.
TURN_MARGIN = 1.5

#: Spawns nearer the player than this are the one they stand on.
NEAR_SPAWN = 2.5

PATTERNS = ("static", "wide", "adad", "jiggle", "crouch_peek", "popup")


@dataclass(frozen=True)
class Drill:
    id: str
    name: str
    description: str
    pattern: str
    #: The map the drill is on, or None for any map (targets on spawn points).
    map: str | None
    #: A station in that map's `drills` list. Ignored when `map` is None.
    station: str | None = None
    seconds: float = 60.0
    hp: int = 100
    head_only: bool = False
    #: Seconds a downed target stays down. Shorter than the free range's three:
    #: a drill is a minute long, and a lane standing empty is a minute wasted.
    respawn: float = 1.0
    seed: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "pattern": self.pattern,
            "map": self.map,
            "station": self.station,
            "seconds": self.seconds,
            "hp": self.hp,
            "headOnly": self.head_only,
            "respawn": self.respawn,
        }


DRILLS: tuple[Drill, ...] = (
    Drill(
        id="aim_static",
        name="Static Flicks",
        description="Still targets at 10 and 25 cubes. Flick, fire, reset.",
        pattern="static",
        map="hd_aim",
        station="close",
    ),
    Drill(
        id="aim_strafe",
        name="Strafe Gallery",
        description="Targets strafe end to end of their rail. Track, then fire.",
        pattern="wide",
        map="hd_aim",
        station="gallery",
    ),
    Drill(
        id="aim_adad",
        name="AD Spam",
        description="Short, random direction changes. Fire as they stop.",
        pattern="adad",
        map="hd_aim",
        station="gallery",
        seed=2,
    ),
    Drill(
        id="aim_jiggle",
        name="Jiggle Peek",
        description="Very short peeks back and forth across a narrow gap.",
        pattern="jiggle",
        map="hd_aim",
        station="close",
        seed=3,
    ),
    Drill(
        id="aim_crouch",
        name="Crouch Peek",
        description="AD strafes with crouches mixed in. The head moves.",
        pattern="crouch_peek",
        map="hd_aim",
        station="gallery",
        seed=4,
    ),
    Drill(
        id="aim_popup",
        name="Pop-Up Reaction",
        description="Targets appear for about a second. Scores reaction time.",
        pattern="popup",
        map="hd_aim",
        station="close",
        seed=5,
    ),
    Drill(
        id="aim_long",
        name="Long Lane",
        description="Wide strafes at 50 and 100 cubes. Scope, lead, fire.",
        pattern="wide",
        map="hd_aim",
        station="long",
        seed=6,
    ),
    Drill(
        id="aim_heads",
        name="Heads Only",
        description="AD strafes where only a headshot does damage.",
        pattern="adad",
        map="hd_aim",
        station="gallery",
        head_only=True,
        seed=7,
    ),
    Drill(
        id="spawn_strafe",
        name="Strafe on Spawns",
        description="Any map: targets strafe on the spawn points nearest you.",
        pattern="adad",
        map=None,
        seed=8,
    ),
)

DRILL_BY_ID: dict[str, Drill] = {d.id: d for d in DRILLS}


class DrillError(ValueError):
    """A drill that cannot be built here: unknown id, map or station."""


@lru_cache(maxsize=8)
def _map(name: str) -> tuple[physics.World, Any, dict[str, Any]]:
    """The simulation world, the parsed map and the raw source of `name`."""
    cmap = mapsource.load_bundled(name)
    path = mapsource.source_path(name)
    if cmap is None or path is None:
        raise DrillError(f"no such map: {name}")
    source = json.loads(path.read_text(encoding="utf-8"))
    return physics.World.from_map(cmap), cmap, source


def stations(map_name: str) -> list[dict[str, Any]]:
    """The drill stations a map declares, as written in its source."""
    _world, _cmap, source = _map(map_name)
    declared = source.get("drills", [])
    return declared if isinstance(declared, list) else []


def drills_for(map_name: str | None) -> list[Drill]:
    """The drills a player on `map_name` can start: that map's own, plus the
    any-map ones. With no map, all of them."""
    return [d for d in DRILLS if map_name is None or d.map in (None, map_name)]


def _stand(world: physics.World, x: float, y: float, yaw: float) -> physics.PlayerState:
    """A body standing on the world at cell `(x, y)`, facing `yaw`."""
    state = physics.spawn_at(world, SimpleNamespace(x=int(x), y=int(y), yaw=0.0))
    state.yaw = yaw
    return state


def _posts(
    drill: Drill, map_name: str, near: tuple[float, float] | None
) -> tuple[list[tuple[float, float, float]], tuple[float, float] | None]:
    """`[(x, y, half)]` for every target, plus the shooter's cell, if fixed."""
    world, cmap, _source = _map(map_name)
    if drill.map is not None:
        station = next(
            (s for s in stations(map_name) if s.get("id") == drill.station), None
        )
        if station is None:
            raise DrillError(f"{map_name} has no drill station {drill.station!r}")
        sx, sy = station["shooter"]
        posts = [(float(p[0]), float(p[1]), float(p[2])) for p in station["posts"]]
        return posts[:MAX_TARGETS], (float(sx), float(sy))
    if near is None:
        raise DrillError(f"{drill.id} needs the player's position")
    points = []
    for entity in cmap.entities:
        if entity.name != "playerstart":
            continue
        d = math.hypot(entity.x + 0.5 - near[0], entity.y + 0.5 - near[1])
        if d > NEAR_SPAWN:
            points.append((d, float(entity.x), float(entity.y)))
    points.sort()
    return [(x, y, SPAWN_POST_HALF) for _d, x, y in points[:MAX_TARGETS]], None


def _ticks(rng: random.Random, low: float, high: float) -> int:
    return max(1, round(rng.uniform(low, high) * TICK_RATE))


def schedule(
    world: physics.World,
    state: physics.PlayerState,
    half: float,
    pattern: str,
    ticks: int,
    rng: random.Random,
    samples: int = 0,
) -> tuple[list[list[int]], list[list[float]]]:
    """Simulate one target and return its input segments.

    `state` is advanced in place, one `TICK` per tick, exactly as a client will
    replay it: the input for tick `k` is decided from the state *before* step `k`,
    then the step runs. With `samples > 0`, also returns `[tick, x, y, z]` every
    `samples` ticks, for the conformance fixture.
    """
    if pattern not in PATTERNS:
        raise DrillError(f"unknown pattern {pattern!r}")
    post_x, post_y = state.x, state.y
    # Strafe +1 moves along (-sin, cos) of the yaw, see `physics._wish_direction`.
    side_x, side_y = -math.sin(state.yaw), math.cos(state.yaw)
    direction = rng.choice((-1, 1))
    hold_until = 0
    crouch = False
    crouch_until = 0
    visible = pattern != "popup"
    show_until = _ticks(rng, 0.4, 1.6) if pattern == "popup" else 0

    segments: list[list[int]] = []
    trace: list[list[float]] = []
    current: tuple[int, int, int, int] | None = None
    for k in range(ticks):
        lateral = (state.x - post_x) * side_x + (state.y - post_y) * side_y
        at_edge = lateral * direction >= half - TURN_MARGIN
        strafe = 0
        if pattern == "wide":
            if at_edge:
                direction = -direction
            strafe = direction
        elif pattern in ("adad", "jiggle", "crouch_peek"):
            if k >= hold_until or at_edge:
                direction = -direction if k > 0 or at_edge else direction
                hold_until = k + (
                    _ticks(rng, 0.10, 0.22)
                    if pattern == "jiggle"
                    else _ticks(rng, 0.25, 0.6)
                )
            strafe = direction
            if pattern == "crouch_peek" and k >= crouch_until:
                crouch = rng.random() < 0.35 if not crouch else False
                crouch_until = k + _ticks(rng, 0.3, 0.8)
        elif pattern == "popup":
            if k >= show_until:
                visible = not visible
                show_until = k + (
                    _ticks(rng, 0.8, 1.4) if visible else _ticks(rng, 0.6, 1.6)
                )
        entry = (strafe, int(crouch), 0, int(visible))
        if entry != current:
            segments.append([k, *entry])
            current = entry
        if samples and k % samples == 0:
            trace.append([k, state.x, state.y, state.z])
        physics.step(
            world, state, physics.MoveInput(strafe=float(strafe), crouch=crouch), TICK
        )
    if samples:
        trace.append([ticks, state.x, state.y, state.z])
    return segments, trace


def expand(
    drill_id: str,
    map_name: str | None = None,
    near: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Everything a client needs to run `drill_id`.

    `map_name` is required for an any-map drill and must match the drill's own
    map otherwise. `near` is the player's position, which only an any-map drill
    reads (its targets stand on the spawns nearest it).
    """
    drill = DRILL_BY_ID.get(drill_id)
    if drill is None:
        raise DrillError(f"no such drill: {drill_id}")
    where = drill.map or map_name
    if where is None:
        raise DrillError(f"{drill_id} runs on any map; say which")
    if drill.map is not None and map_name not in (None, drill.map):
        raise DrillError(f"{drill_id} runs on {drill.map}, not {map_name}")
    world, _cmap, _source = _map(where)
    posts, shooter_cell = _posts(drill, where, near)
    if not posts:
        raise DrillError(f"{where} has nowhere to put a target")

    aim_x, aim_y = shooter_cell if shooter_cell else (near or (0.0, 0.0))
    if shooter_cell:
        aim_x, aim_y = aim_x + 0.5, aim_y + 0.5
    rng = random.Random(f"{drill.id}:{where}:{drill.seed}")
    ticks = round(drill.seconds * TICK_RATE)
    targets = []
    for i, (px, py, half) in enumerate(posts):
        yaw = math.atan2(aim_y - (py + 0.5), aim_x - (px + 0.5))
        state = _stand(world, px, py, yaw)
        start = (state.x, state.y, state.z)
        segments, _trace = schedule(world, state, half, drill.pattern, ticks, rng)
        targets.append(
            {
                "id": f"t{i}",
                "x": start[0],
                "y": start[1],
                "z": start[2],
                "yaw": yaw,
                "segments": segments,
            }
        )

    shooter = None
    if shooter_cell:
        centre_x = sum(p[0] + 0.5 for p in posts) / len(posts)
        centre_y = sum(p[1] + 0.5 for p in posts) / len(posts)
        stand = _stand(
            world,
            shooter_cell[0],
            shooter_cell[1],
            math.atan2(centre_y - aim_y, centre_x - aim_x),
        )
        shooter = {"x": stand.x, "y": stand.y, "z": stand.z, "yaw": stand.yaw}

    return {
        **drill.to_dict(),
        "map": where,
        "tickRate": TICK_RATE,
        "ticks": ticks,
        "shooter": shooter,
        "targets": targets,
    }


# ---- scoring ---------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarise(events: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    """Score one run from its event log. The reference both clients port.

    Events, in time order:

    - `{"t", "kind": "appear", "target"}`: a target became hittable (the drill
      began, it stood back up, or a pop-up showed).
    - `{"t", "kind": "hide", "target"}`: it stopped being hittable without dying.
    - `{"t", "kind": "shot", "hits": [{"target", "head", "killed"}]}`: one trigger
      pull, with one entry per pellet that struck a target. Empty is a miss.

    Accuracy is per trigger pull (a shotgun blast that lands one pellet is a hit);
    headshot share is per pellet. Time to kill runs from the first hit in a
    target's appearance to the kill; reaction from the appearance to its first
    hit. A target hit again after it is down cannot happen here, because the
    client only reports hits on hittable targets.
    """
    shots = hit_shots = hits = head_hits = kills = 0
    shown_at: dict[str, float] = {}
    first_hit: dict[str, float] = {}
    ttks: list[float] = []
    reactions: list[float] = []
    for event in events:
        t = float(event["t"])
        kind = event["kind"]
        if kind == "appear":
            shown_at[str(event["target"])] = t
            first_hit.pop(str(event["target"]), None)
        elif kind == "hide":
            shown_at.pop(str(event["target"]), None)
            first_hit.pop(str(event["target"]), None)
        elif kind == "shot":
            shots += 1
            landed = event.get("hits") or []
            if landed:
                hit_shots += 1
            for hit in landed:
                target = str(hit["target"])
                hits += 1
                if hit.get("head"):
                    head_hits += 1
                if target in shown_at and target not in first_hit:
                    first_hit[target] = t
                    reactions.append(t - shown_at[target])
                if hit.get("killed"):
                    kills += 1
                    if target in first_hit:
                        ttks.append(t - first_hit[target])
                    shown_at.pop(target, None)
                    first_hit.pop(target, None)

    def ratio(a: int, b: int) -> float:
        return a / b if b else 0.0

    return {
        "shots": shots,
        "hitShots": hit_shots,
        "accuracy": ratio(hit_shots, shots),
        "hits": hits,
        "headHits": head_hits,
        "headshotShare": ratio(head_hits, hits),
        "kills": kills,
        "killsPerMinute": kills * 60.0 / seconds if seconds > 0 else 0.0,
        "medianTtk": _median(ttks),
        "medianReaction": _median(reactions),
    }
