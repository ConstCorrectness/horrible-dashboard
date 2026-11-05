"""Fly the native client's benchmark path over every map and tier.

Runs the release `hassault-native --bench=<map>` once per (map, tier), against a
node that serves the maps (``--server``), collects the ``BENCH {json}`` line
each run prints, and writes them to a JSONL file plus a Markdown table in the
shape of the baseline in ``docs/modules/hassault.mdx`` ("Measuring
performance"). The browser's half comes from its ``bench`` console command
(or ``window.__hassaultBench``) and has the same JSON shape.

Run it on a quiet machine: a bench beside a compile measures the compile.

    uv run --no-sync python scripts/hassault_bench_native.py --server http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATHS = ROOT / "packages/core/src/modules/hassault/bench-paths.json"
BINARY = ROOT / "apps/native-fps/target/release/hassault-native.exe"


def run_one(
    binary: Path, server: str, game_map: str, tier: str, timeout: float
) -> dict | None:
    cmd = [
        str(binary),
        f"--server={server}",
        f"--bench={game_map}",
        f"--set=hassault.video.quality={tier}",
    ]
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        print(f"  {game_map} {tier}: timed out after {timeout:.0f}s", file=sys.stderr)
        return None
    for line in done.stdout.splitlines():
        if line.startswith("BENCH "):
            return json.loads(line[len("BENCH ") :])
    tail = "\n".join(done.stderr.splitlines()[-5:])
    print(
        f"  {game_map} {tier}: no result (exit {done.returncode})\n{tail}",
        file=sys.stderr,
    )
    return None


def table(results: list[dict]) -> str:
    head = (
        "| Map | Client | Tier | Res | fps | p50 ms | p99 ms | CPU p50/p99 ms | GPU p50/p99 ms | Draws (max) | Tris (max) |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = []
    for r in results:
        gpu = (
            f"{r['gpuP50']:.2f} / {r['gpuP99']:.2f}"
            if r.get("gpuP50") is not None and r.get("gpuP99") is not None
            else "n/a"
        )
        rows.append(
            f"| {r['map']} | {r['client']} | {r['tier']} | {r.get('width', 0)}x{r.get('height', 0)} | {r['fps']:.0f} | {r['p50']:.2f} | {r['p99']:.2f} "
            f"| {r['cpuP50']:.2f} / {r['cpuP99']:.2f} | {gpu} | {r['drawCallsMax']} | {r['trianglesMax'] // 1000}k |"
        )
    return "\n".join([head, *rows])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", default="http://127.0.0.1:8123")
    parser.add_argument("--binary", type=Path, default=BINARY)
    parser.add_argument("--tiers", default="medium,high")
    parser.add_argument("--maps", default="", help="comma-separated; default all")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", type=Path, default=Path("hassault-bench-native.jsonl"))
    args = parser.parse_args()

    if not args.binary.exists():
        print(
            f"no binary at {args.binary}; cargo build --release first", file=sys.stderr
        )
        return 2
    maps = (
        [m for m in args.maps.split(",") if m]
        if args.maps
        else sorted(json.loads(PATHS.read_text(encoding="utf-8"))["maps"])
    )
    tiers = [t for t in args.tiers.split(",") if t]

    results: list[dict] = []
    with args.out.open("w", encoding="utf-8", newline="\n") as out:
        for game_map in maps:
            for tier in tiers:
                print(f"bench {game_map} {tier}", file=sys.stderr)
                result = run_one(args.binary, args.server, game_map, tier, args.timeout)
                if result is not None:
                    results.append(result)
                    out.write(json.dumps(result) + "\n")
                    out.flush()
    print(table(results))
    return 0 if len(results) == len(maps) * len(tiers) else 1


if __name__ == "__main__":
    raise SystemExit(main())
