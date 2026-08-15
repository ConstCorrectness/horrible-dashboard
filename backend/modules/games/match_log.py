"""Node-side match history with **loadout attribution** — which harness version
(and model) played each game, and how it went.

This is the data behind the harness-progression loop: the LlmHarness panel shows a
per-version W/L strip so you can tell whether your latest branch actually plays
better. Appended by `client.py` on every `game_over`; the following
`rating_update` (rating games only) attaches the delta to the newest entry.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from backend import paths

logger = logging.getLogger(__name__)

MAX_ENTRIES = 500


def _log_path() -> Path:
    return paths.data_dir() / "games_match_log.json"


def _read() -> list[dict[str, Any]]:
    path = _log_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except ValueError:
        logger.warning("games match log is corrupt; starting empty")
        return []


def _write(entries: list[dict[str, Any]]) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries[-MAX_ENTRIES:], indent=2), encoding="utf-8")


def append_entry(
    *,
    game_id: str,
    table_id: str,
    seat: int | None,
    winner: int | None,
    loadout_version: str | None,
    model_label: str | None,
    replay_id: str | None = None,
) -> None:
    result = "draw"
    if seat is not None and winner is not None:
        result = "win" if winner == seat else "loss"
    entries = _read()
    entries.append(
        {
            "ts": time.time(),
            "game_id": game_id,
            "table_id": table_id,
            "seat": seat,
            "result": result,
            "loadout_version": loadout_version,
            "model_label": model_label,
            "replay_id": replay_id,
            "rating_delta": None,
        }
    )
    _write(entries)


def attach_rating(game_id: str, delta: float, rating: float, tier: str | None) -> None:
    """Stamp the newest un-stamped entry for `game_id` with its rating movement."""
    entries = _read()
    for entry in reversed(entries):
        if entry.get("game_id") == game_id and entry.get("rating_delta") is None:
            entry["rating_delta"] = delta
            entry["rating"] = rating
            entry["tier"] = tier
            break
    _write(entries)


def list_entries(game_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    entries = _read()
    if game_id:
        entries = [e for e in entries if e.get("game_id") == game_id]
    return list(reversed(entries[-limit:]))


def version_stats(game_id: str) -> dict[str, dict[str, int]]:
    """Per-loadout-version W/L/D for a game — the panel's version strip."""
    stats: dict[str, dict[str, int]] = {}
    for entry in _read():
        if entry.get("game_id") != game_id:
            continue
        version = str(entry.get("loadout_version") or "?")
        row = stats.setdefault(version, {"win": 0, "loss": 0, "draw": 0})
        result = str(entry.get("result") or "draw")
        if result in row:
            row[result] += 1
    return stats
