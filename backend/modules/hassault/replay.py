"""Match Replay & Spectator Demo Recording Engine (.hrec).

Tick-accurate demo recording capturing entity state vectors, combat effects,
utility detonations, bomb objectives, and kill events for GOTV-style spectator
theater, match replays, and caster broadcast analysis.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger("hassault.replay")

REPLAY_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".data" / "hassault" / "replays"
REPLAY_DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".data" / "hassault" / "replays.db"


@contextmanager
def get_replay_db() -> Generator[sqlite3.Connection, None, None]:
    REPLAY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(REPLAY_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_replay_db() -> None:
    """Initialize the replay index SQLite database."""
    with get_replay_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hassault_replays (
                id           TEXT PRIMARY KEY,
                room_id      TEXT NOT NULL,
                map_name     TEXT NOT NULL,
                mode         TEXT NOT NULL,
                duration     REAL NOT NULL DEFAULT 0.0,
                ticks        INTEGER NOT NULL DEFAULT 0,
                winner_team  INTEGER,
                player_count INTEGER NOT NULL DEFAULT 0,
                players_json TEXT NOT NULL DEFAULT '[]',
                file_path    TEXT NOT NULL,
                created_at   REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_replays_created
            ON hassault_replays(created_at DESC)
            """
        )


class ReplayRecorder:
    """Tick-accurate match recording stream."""

    def __init__(
        self,
        room_id: str,
        map_name: str,
        mode_name: str,
        tick_rate: int = 20,
    ) -> None:
        self.replay_id = uuid.uuid4().hex
        self.room_id = room_id
        self.map_name = map_name
        self.mode_name = mode_name
        self.tick_rate = tick_rate
        self.started_at = time.time()
        self.players: dict[str, dict[str, Any]] = {}
        self.frames: list[dict[str, Any]] = []
        self._finalized = False

    def register_player(self, player_id: str, name: str, team: int, is_bot: bool = False) -> None:
        self.players[player_id] = {
            "id": player_id,
            "name": name,
            "team": team,
            "is_bot": is_bot,
        }

    def record_tick(
        self,
        tick: int,
        timestamp: float,
        players: list[dict[str, Any]],
        shared: dict[str, Any],
        scores: list[int],
    ) -> None:
        if self._finalized:
            return

        # Compact player records: [id, x, y, z, yaw, pitch, health, armour, weapon, is_shooting]
        compact_players = []
        for p in players:
            pid = p.get("id", "")
            x = round(float(p.get("x", 0.0)), 2)
            y = round(float(p.get("y", 0.0)), 2)
            z = round(float(p.get("z", 0.0)), 2)
            yaw = round(float(p.get("yaw", 0.0)), 1)
            pitch = round(float(p.get("pitch", 0.0)), 1)
            hp = int(p.get("health", 100))
            armour = int(p.get("armour", 0))
            weap = str(p.get("weapon", ""))
            alive = bool(p.get("alive", True))
            crouch = bool(p.get("crouch", False))
            firing = bool(p.get("firing", False))

            compact_players.append({
                "id": pid,
                "x": x,
                "y": y,
                "z": z,
                "yaw": yaw,
                "pitch": pitch,
                "hp": hp,
                "armour": armour,
                "weap": weap,
                "alive": alive,
                "crouch": crouch,
                "firing": firing,
            })

        frame = {
            "tick": tick,
            "t": round(timestamp, 3),
            "scores": list(scores),
            "players": compact_players,
            "fx": list(shared.get("fx", [])),
            "nades": list(shared.get("nades", [])),
            "zones": list(shared.get("zones", [])),
            "mode": shared.get("mode"),
        }
        self.frames.append(frame)

    def finish(self, winner_team: int | None = None) -> str:
        if self._finalized:
            return self.replay_id
        self._finalized = True

        duration = max(0.0, time.time() - self.started_at)
        total_ticks = len(self.frames)

        data = {
            "version": 1,
            "id": self.replay_id,
            "room_id": self.room_id,
            "map_name": self.map_name,
            "mode": self.mode_name,
            "tick_rate": self.tick_rate,
            "duration": round(duration, 2),
            "ticks": total_ticks,
            "winner_team": winner_team,
            "started_at": self.started_at,
            "players": list(self.players.values()),
            "frames": self.frames,
        }

        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        file_path = REPLAY_DIR / f"{self.replay_id}.hrec"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
        except Exception as e:
            logger.error("Failed to write replay file: %s", e)

        try:
            init_replay_db()
            with get_replay_db() as conn:
                conn.execute(
                    """
                    INSERT INTO hassault_replays (
                        id, room_id, map_name, mode, duration, ticks,
                        winner_team, player_count, players_json, file_path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.replay_id,
                        self.room_id,
                        self.map_name,
                        self.mode_name,
                        round(duration, 2),
                        total_ticks,
                        winner_team,
                        len(self.players),
                        json.dumps(list(self.players.values())),
                        str(file_path),
                        self.started_at,
                    ),
                )
        except Exception as e:
            logger.error("Failed to index replay in database: %s", e)

        return self.replay_id


def list_replays(limit: int = 50) -> list[dict[str, Any]]:
    init_replay_db()
    with get_replay_db() as conn:
        rows = conn.execute(
            """
            SELECT id, room_id, map_name, mode, duration, ticks,
                   winner_team, player_count, players_json, created_at
            FROM hassault_replays
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["players"] = json.loads(d.pop("players_json"))
        except Exception:
            d["players"] = []
        result.append(d)
    return result


def get_replay_metadata(replay_id: str) -> dict[str, Any] | None:
    init_replay_db()
    with get_replay_db() as conn:
        row = conn.execute(
            """
            SELECT id, room_id, map_name, mode, duration, ticks,
                   winner_team, player_count, players_json, file_path, created_at
            FROM hassault_replays
            WHERE id = ?
            """,
            (replay_id,),
        ).fetchone()

    if not row:
        return None
    d = dict(row)
    try:
        d["players"] = json.loads(d.pop("players_json"))
    except Exception:
        d["players"] = []
    return d


def get_replay_data(replay_id: str) -> dict[str, Any] | None:
    meta = get_replay_metadata(replay_id)
    if not meta:
        return None
    file_path = Path(meta["file_path"])
    if not file_path.is_file():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to read replay file %s: %s", file_path, e)
        return None


def delete_replay(replay_id: str) -> bool:
    meta = get_replay_metadata(replay_id)
    if not meta:
        return False
    file_path = Path(meta["file_path"])
    if file_path.is_file():
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass
    with get_replay_db() as conn:
        conn.execute("DELETE FROM hassault_replays WHERE id = ?", (replay_id,))
    return True
