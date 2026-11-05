"""Let bots play a map for a while with no one watching, and report where they went.

    uv run python tools/hassault_bot_soak.py hd_dust2 [seconds] [bots] [mode]
        [--json out.json] [--seed N]

Opens a room the way the server does (`assets.load_map`, `World.from_map`,
objectives, pickups), fills it with bots and ticks it. Reports, for the map's own
cover nodes (`bots.TACTICAL_COVER_NODES`) and bomb sites, how many bot-ticks each
one got, how much of the walkable floor was stood on, and every time a bot sat
within `STUCK_RADIUS` of one spot for `SIT_SECONDS` or more: where, and when in
the round. In `defuse` it also plays whole matches back to back (a fresh room per
match) and reports each round's outcome, which site was hit and how much of the
round clock was left.

It cannot say a map is good, but it does say a place is unreachable *to a bot*,
which the walking-path lint cannot. Bots go straight for objectives, so a node
with few visits may be skipped rather than unreachable: judge reach with a
single-bot walk test (`test_hassault_nav.py`), not with these counts.

Two things a live server does that a loop calling `simulate` must copy, or the
bots behave differently here than there: the room's per-tick lists (`noises`,
`fx`) are emptied after every tick, as `_broadcast` does.
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.modules.hassault import assets, bots, maplint, modes, pickups  # noqa: E402
from backend.modules.hassault.match import MatchRoom  # noqa: E402
from backend.modules.hassault.modes import objectives  # noqa: E402
from backend.modules.hassault.physics import World  # noqa: E402

TICK = 1 / 20
#: A node counts as visited when a bot's centre is this close, in cubes.
NODE_RADIUS = 8.0
#: Sitting inside this radius for this long is reported.
STUCK_RADIUS = 2.0
SIT_SECONDS = 10.0
#: A sit this long in a live round, away from a site, is called stuck.
STUCK_SECONDS = 20.0


def build_room(name, mode):
    cgz = assets.load_map(name)
    world = World.from_map(cgz)
    game_mode = modes.build(mode)
    placed = objectives.place(world, cgz)
    room = MatchRoom(
        "soak",
        name,
        world,
        cgz.spawns(),
        pickups.place(world, cgz.entities),
        mode=game_mode,
        objectives=placed,
    )
    return cgz, world, room, placed


def _phase(room):
    state = getattr(room.mode, "state", None)
    return getattr(state, "phase", "live")


def _places(name, sites):
    out = [(x, y, label) for x, y, label in bots.TACTICAL_COVER_NODES.get(name, [])]
    out += [(s.x, s.y, f"site {s.id}") for s in sites]
    return out


def _nearest_label(places, x, y):
    if not places:
        return "-"
    px, py, label = min(places, key=lambda p: math.hypot(p[0] - x, p[1] - y))
    return f"{label} ({math.hypot(px - x, py - y):.0f})"


def soak(name, seconds, count, mode, seed):
    random.seed(seed)
    standable = None
    seen = set()
    visits = Counter()
    site_visits = Counter()
    sits = []
    rounds = []
    t = 0.0
    matches = 0
    while t < seconds:
        cgz, world, room, placed = build_room(name, mode)
        room.rng.seed(seed + matches)
        matches += 1
        if standable is None:
            standable = maplint._standable(world)
        sites = list(getattr(placed, "sites", []) or [])
        places = _places(name, sites)
        nodes = bots.TACTICAL_COVER_NODES.get(name, [])
        members = bots.add_bots(room, count, skill="normal")
        anchor = {b.id: (b.state.x, b.state.y, t) for b in members}
        prev = getattr(room.mode, "state", None)
        live_since = None
        plant = None
        while t < seconds:
            room.simulate(TICK)
            room.noises.clear()
            room.fx.clear()
            t += TICK
            state = getattr(room.mode, "state", None)
            phase = _phase(room)
            if state is not None and prev is not None:
                if phase == "live" and prev.phase != "live":
                    live_since = t
                    plant = None
                if (
                    state.bomb.state == "planted"
                    and prev.bomb.state != "planted"
                    and live_since is not None
                ):
                    plant = (state.bomb.site, t - live_since)
                if prev.phase == "live" and phase in ("post", "over"):
                    bomb = prev.bomb
                    if state.bomb.state == "defused":
                        how = "defused"
                    elif bomb.state == "planted":
                        how = "exploded"
                    elif prev.remaining <= TICK * 1.5:
                        how = "time_out"
                    else:
                        how = "eliminated"
                    rounds.append(
                        {
                            "match": matches,
                            "round": state.round,
                            "winner": "T"
                            if state.last_winner == prev.attackers
                            else "CT",
                            "how": how,
                            "clock_left": round(prev.remaining, 1)
                            if plant is None
                            else None,
                            "plant_site": plant[0] if plant else None,
                            "plant_at": round(plant[1], 1) if plant else None,
                            "length": round(t - (live_since or t), 1),
                            **_plan(room),
                        }
                    )
            prev = state
            live = phase in ("live", "warmup")
            for b in members:
                if not b.alive or not live:
                    anchor[b.id] = (b.state.x, b.state.y, t)
                    continue
                seen.add((int(b.state.x), int(b.state.y)))
                for x, y, label in nodes:
                    if math.hypot(b.state.x - x, b.state.y - y) <= NODE_RADIUS:
                        visits[label] += 1
                for s in sites:
                    if math.hypot(b.state.x - s.x, b.state.y - s.y) <= s.radius:
                        site_visits[(s.id, "T" if _attacking(room, b) else "CT")] += 1
                ax, ay, since = anchor[b.id]
                if math.hypot(b.state.x - ax, b.state.y - ay) > STUCK_RADIUS:
                    if t - since >= SIT_SECONDS:
                        sits.append(_sit(b, room, ax, ay, since, t, places, live_since))
                    anchor[b.id] = (b.state.x, b.state.y, t)
            if phase == "over":
                break
        for b in members:
            ax, ay, since = anchor[b.id]
            if b.alive and t - since >= SIT_SECONDS:
                sits.append(_sit(b, room, ax, ay, since, t, places, live_since))
    return {
        "map": name,
        "mode": mode,
        "bots": count,
        "seconds": seconds,
        "matches": matches,
        "floor_cells": len(standable),
        "floor_stood_on": len(seen & standable),
        "cover_visits": {
            label: visits[label]
            for _, _, label in bots.TACTICAL_COVER_NODES.get(name, [])
        },
        "site_visits": {f"{k[0]}/{k[1]}": v for k, v in sorted(site_visits.items())},
        "sits": sits,
        "rounds": rounds,
    }


def _plan(room):
    """What the bots' team plan was for the round, if they had one."""
    t = getattr(room, "bot_tactics", None)
    if t is None:
        return {}
    mine = {lu.id for lu in t.lineup.values()}
    return {
        "plan_site": t.site,
        "style": t.style,
        "lineups": len(mine),
        "thrown": len(t.thrown & mine),
        "went": t.go,
    }


def _attacking(room, b):
    state = getattr(room.mode, "state", None)
    return state is not None and b.team == state.attackers


def _sit(b, room, x, y, since, t, places, live_since):
    return {
        "bot": b.name,
        "team": "T" if _attacking(room, b) else "CT",
        "x": round(x, 1),
        "y": round(y, 1),
        "near": _nearest_label(places, x, y),
        "seconds": round(t - since, 1),
        "round_time": round(since - live_since, 1) if live_since is not None else None,
    }


def print_report(r):
    print(
        f"{r['map']} ({r['mode']}), {r['bots']} bots, {r['seconds']:.0f} s simulated, {r['matches']} match(es)"
    )
    pct = 100 * r["floor_stood_on"] / max(1, r["floor_cells"])
    print(
        f"  walkable floor stood on: {r['floor_stood_on']} of {r['floor_cells']} cells ({pct:.0f}%)"
    )
    print(f"  visits to cover nodes (bot-ticks within {NODE_RADIUS:.0f} cubes):")
    for label, n in r["cover_visits"].items():
        print(f"    {label:22s} {n:6d}{'   <-- never' if not n else ''}")
    if r["site_visits"]:
        print("  bot-ticks on sites:")
        for key, n in r["site_visits"].items():
            print(f"    {key:8s} {n:6d}")
    stuck = [
        s
        for s in r["sits"]
        if s["seconds"] >= STUCK_SECONDS and "site" not in s["near"]
    ]
    print(
        f"  sits >= {SIT_SECONDS:.0f} s: {len(r['sits'])}, of them {len(stuck)} >= {STUCK_SECONDS:.0f} s off-site"
    )
    for s in sorted(r["sits"], key=lambda s: -s["seconds"])[:8]:
        print(
            f"    {s['team']:2s} {s['seconds']:5.0f} s at ({s['x']:.0f},{s['y']:.0f}) near {s['near']}"
            f" from round t={s['round_time']}"
        )
    if r["rounds"]:
        rs = r["rounds"]
        wins = Counter(x["winner"] for x in rs)
        how = Counter(x["how"] for x in rs)
        planted = [x for x in rs if x["plant_site"]]
        print(
            f"  rounds: {len(rs)}  T {wins['T']} / CT {wins['CT']}  "
            + ", ".join(f"{k} {v}" for k, v in how.items())
        )
        if planted:
            at = sorted(x["plant_at"] for x in planted)
            print(
                f"  plants: {len(planted)} ({', '.join(f'{k} {v}' for k, v in Counter(x['plant_site'] for x in planted).items())}),"
                f" at round t= median {at[len(at) // 2]:.0f} s, max {at[-1]:.0f} s"
            )
        left = sorted(x["clock_left"] for x in rs if x["clock_left"] is not None)
        if left:
            print(
                f"  clock left at an unplanted round's end: median {left[len(left) // 2]:.0f} s, min {left[0]:.0f} s"
            )
        if any("plan_site" in x for x in rs):
            hit = Counter((x.get("plan_site"), x.get("style")) for x in rs)
            thrown = sum(x.get("thrown", 0) for x in rs)
            planned = sum(x.get("lineups", 0) for x in rs)
            print(
                "  plans: "
                + ", ".join(
                    f"{k[0]}/{k[1]} {v}" for k, v in sorted(hit.items(), key=str)
                )
                + f"; lineups thrown {thrown} of {planned} assigned"
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("seconds", nargs="?", type=float, default=120.0)
    ap.add_argument("bots", nargs="?", type=int, default=8)
    ap.add_argument("mode", nargs="?", default="dm")
    ap.add_argument("--json")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    started = time.perf_counter()
    r = soak(a.name, a.seconds, a.bots, a.mode, a.seed)
    r["wall_seconds"] = round(time.perf_counter() - started, 1)
    print_report(r)
    print(f"  ({r['wall_seconds']:.0f} s wall)")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=1)


if __name__ == "__main__":
    main()
