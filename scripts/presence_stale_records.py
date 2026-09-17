"""List (and, only when asked, delete) stale records in the shared presence directory.

Test runs used to publish a record per test into the live `presence` collection, each
under a fresh person key from a temp data dir (fixed in `backend/tests/conftest.py`).
Those records are harmless to lookups — they go stale after `TTL_SECONDS` and nobody
holds their keys — but they clutter a directory other people's nodes read.

A record is listed when it is **stale** (older than `directory.TTL_SECONDS`) and its
`person_id` is **not** one you keep. Nothing is deleted unless you pass `--delete`,
and then only the records listed in that same run.

    uv run python scripts/presence_stale_records.py
    uv run python scripts/presence_stale_records.py --keep eov3ae5c4zkq2txl --keep 7io6kwhpk4472xmo
    uv run python scripts/presence_stale_records.py --name MSI --name horribleComputer --delete

`--name` narrows to records with those display names, so a run cannot touch a
stranger's stale record just because it is stale.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import atlas  # noqa: E402 - after the path insert
from backend.modules.social import directory  # noqa: E402

#: This user's own people: MSI and horribleComputer. Their records are never listed.
DEFAULT_KEEP = ("eov3ae5c4zkq2txl", "7io6kwhpk4472xmo")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="append", default=[], help="person_id to keep")
    parser.add_argument(
        "--name", action="append", default=[], help="only this display name"
    )
    parser.add_argument("--delete", action="store_true", help="delete what is listed")
    args = parser.parse_args()

    collection = atlas.collection(directory.COLLECTION)
    if collection is None:
        print(
            "Atlas is not configured (ATLAS_DB_USER / ATLAS_DB_PASS / ATLAS_CLUSTER_HOST)."
        )
        return 1

    keep = set(DEFAULT_KEEP) | set(args.keep)
    cutoff = time.time() - directory.TTL_SECONDS
    query: dict = {"updated_at": {"$lt": cutoff}, "person_id": {"$nin": sorted(keep)}}
    if args.name:
        query["display_name"] = {"$in": args.name}

    stale = [rec async for rec in collection.find(query).sort("updated_at", 1)]
    now = time.time()
    for rec in stale:
        age_h = (now - float(rec.get("updated_at", 0))) / 3600
        print(
            f"{rec.get('person_id')}  {str(rec.get('display_name'))[:24]:<24} "
            f"{age_h:7.1f}h old  {rec.get('addresses')}"
        )
    print(f"\n{len(stale)} stale record(s) not in the keep list")

    if not args.delete or not stale:
        if stale:
            print("Nothing deleted. Re-run with --delete to remove exactly these.")
        return 0
    ids = [rec["_id"] for rec in stale]
    result = await collection.delete_many({"_id": {"$in": ids}})
    print(f"deleted {result.deleted_count} record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
