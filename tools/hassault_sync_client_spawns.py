"""Copy a map's spawns from its JSON into both clients' Train tables.

Run last, after `tools/hassault_fit_placements.py` has settled the JSON:

    uv run python tools/hassault_sync_client_spawns.py hd_dust2 [hd_bank ...]

Offline Train has no server, so each client keeps its own spawn rows
(`world3d.ts` `MAP_CONFIGS`, `world3d.rs` `create_world_3d`) and stands a body on
the mesh there (`groundSpawn` / `ground_spawn`). Those rows drifted from the
JSON: the fit moved a server spawn off a market-stall table and the client row
stayed in it. Each row becomes the JSON spawn's cell and team, and the floor the
server's `spawn_at` stands it on, so Train starts where a match would. Each
client keeps its own team order (the browser lists team 0 first, native team 1),
because both start Train on their first row.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.modules.hassault import mapsource, physics  # noqa: E402
from backend.modules.hassault.cgz import PLAYERSTART, MapEntity  # noqa: E402

TS = ROOT / "packages" / "core" / "src" / "modules" / "hassault" / "world3d.ts"
RS = ROOT / "apps" / "native-fps" / "src" / "world3d.rs"


def rows(name: str) -> list[tuple[int, int, float, float, int]]:
    """(x, y, z, yaw, team) per JSON spawn, z where `spawn_at` stands the body."""
    source = json.loads(mapsource.source_path(name).read_text(encoding="utf-8"))
    world = physics.World.from_map(mapsource.build(source, name=name))
    out = []
    for e in source["entities"]:
        if e["type"] != "playerstart":
            continue
        spawn = MapEntity(type=PLAYERSTART, name="playerstart", x=e["x"], y=e["y"], z=0)
        z = physics.spawn_at(world, spawn).z
        out.append(
            (
                int(e["x"]),
                int(e["y"]),
                float(z),
                float(e.get("yaw", 0)),
                int(e.get("team", 0)),
            )
        )
    return out


def num(v: float, rust: bool) -> str:
    if float(v).is_integer():
        return f"{int(v)}.0" if rust else str(int(v))
    return repr(round(float(v), 3))


def ts_row(r) -> str:
    x, y, z, yaw, team = r
    return f"{{ x: {x}, y: {y}, z: {num(z, False)}, yaw: {num(yaw, False)}, team: {team} }}"


def rs_row(r) -> str:
    x, y, z, yaw, team = r
    return (
        f"SpawnPoint {{ x: {x}.0, y: {y}.0, z: {num(z, True)}, "
        f"yaw: {num(yaw, True)}, team: {team} }}"
    )


def sync_ts(text: str, name: str, spawns) -> str:
    start = text.index(f"\n  {name}: {{\n")
    end = text.index("\n  },\n", start)
    block = text[start:end]
    cla = [r for r in spawns if r[4] == 0]
    rvsf = [r for r in spawns if r[4] != 0]

    def arr(key: str, rs: list) -> str:
        body = "".join(f"        {ts_row(r)},\n" for r in rs)
        return f"      {key}: [\n{body}      ],\n"

    new = (
        "    spawns: {\n"
        + arr("cla", cla)
        + arr("rvsf", rvsf)
        + arr("all", cla + rvsf)
        + "    },\n"
    )
    block2, n = re.subn(
        r"    spawns: \{\n.*?\n    \},\n", new, block + "\n", count=1, flags=re.S
    )
    if n != 1:
        raise SystemExit(f"{name}: no spawns block in world3d.ts")
    return text[:start] + block2[:-1] + text[end:]


def sync_rs(text: str, name: str, spawns) -> str:
    start = text.index("let (spawns, items) = ")
    end = text.index("let bounds = ", start)
    region = text[start:end]
    heads = list(
        re.finditer(r'(?:if|else if) info\.name == "(hd_\w+)" \{|\} else \{\n', region)
    )
    names = [m.group(1) for m in heads]
    # The trailing `else` is the map no branch names: the one missing from the list.
    target = None
    for i, m in enumerate(heads):
        if m.group(1) == name or (m.group(1) is None and name not in names):
            target = i
    if target is None:
        raise SystemExit(f"{name}: no branch in world3d.rs")
    lo = heads[target].end()
    hi = heads[target + 1].start() if target + 1 < len(heads) else len(region)
    branch = region[lo:hi]
    old = re.search(r"(\n( *)SpawnPoint \{[^\n]*\},)+", branch)
    if old is None:
        raise SystemExit(f"{name}: no SpawnPoint rows in its world3d.rs branch")
    indent = old.group(2)
    first_team = int(re.search(r"team: (\d+)", old.group(0)).group(1))
    ordered = [r for r in spawns if r[4] == first_team] + [
        r for r in spawns if r[4] != first_team
    ]
    rows_text = "".join(f"\n{indent}{rs_row(r)}," for r in ordered)
    branch = branch[: old.start()] + rows_text + branch[old.end() :]
    region = region[:lo] + branch + region[hi:]
    return text[:start] + region + text[end:]


def main(names: list[str]) -> None:
    ts = TS.read_bytes().decode("utf-8")
    rs = RS.read_bytes().decode("utf-8")
    ts_crlf, rs_crlf = "\r\n" in ts, "\r\n" in rs
    ts, rs = ts.replace("\r\n", "\n"), rs.replace("\r\n", "\n")
    for name in names:
        spawns = rows(name)
        ts = sync_ts(ts, name, spawns)
        rs = sync_rs(rs, name, spawns)
        print(f"{name}: {len(spawns)} spawns")
    TS.write_bytes((ts.replace("\n", "\r\n") if ts_crlf else ts).encode("utf-8"))
    RS.write_bytes((rs.replace("\n", "\r\n") if rs_crlf else rs).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
