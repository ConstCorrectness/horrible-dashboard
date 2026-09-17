"""Start this machine over as nobody: no sign-in, no identity, no friends.

Removes, from this machine's data directory (`backend.paths.data_dir()`):

- the game-server sign-in (`games_token.json`),
- the person key and profile (`social-person.key`, `social-person.json`),
- the machine key, so it comes back with a fresh node id
  (`network-identity.key`, `network-identity.json`),
- paired peers and outstanding invites (`network-peers.json`, `network-invites.json`),
- the friends list and known machines (the `social_friends` and `social_devices`
  tables in `app.db`; the rest of `app.db` is untouched).

Everything else stays: settings, models, chats, libraries, layouts.

**Stop the backend first.** It holds the identity in memory and would write it
straight back. Dry run by default; pass `--yes` to delete.

    uv run python scripts/reset_identity.py          # show what would go
    uv run python scripts/reset_identity.py --yes    # delete it
"""

from __future__ import annotations

import argparse
import socket
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import paths  # noqa: E402

FILES = (
    "games_token.json",
    "social-person.key",
    "social-person.json",
    "network-identity.key",
    "network-identity.json",
    "network-peers.json",
    "network-invites.json",
)
TABLES = ("social_friends", "social_devices")


def _backend_running(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="actually delete")
    parser.add_argument(
        "--port",
        type=int,
        action="append",
        help="backend port to check is stopped (default: 8000 and 8100)",
    )
    args = parser.parse_args()

    data = paths.data_dir()
    print(f"data directory: {data}")
    files = [data / name for name in FILES if (data / name).exists()]
    db = data / "app.db"
    tables: dict[str, int] = {}
    if db.exists():
        with sqlite3.connect(db) as conn:
            for table in TABLES:
                try:
                    tables[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    pass  # table never created on this machine

    for path in files:
        print(f"  file   {path.name}")
    for table, rows in tables.items():
        print(f"  table  app.db:{table} ({rows} rows)")
    if not files and not tables:
        print("nothing to reset")
        return 0
    if not args.yes:
        print("dry run: pass --yes to delete these")
        return 0

    running = [p for p in (args.port or [8000, 8100]) if _backend_running(p)]
    if running:
        print(
            f"a backend is listening on {', '.join(map(str, running))}: stop it first,"
            " or it will write the identity straight back"
        )
        return 1

    for path in files:
        path.unlink()
    if tables:
        with sqlite3.connect(db) as conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table}")
    print("reset: start the app and sign in (or sign up) to become yourself again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
