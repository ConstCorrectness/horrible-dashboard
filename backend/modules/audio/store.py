"""The mixer state: one persisted routing matrix.

**Why this is not a setting.** `SettingValue` is `string | number | boolean`, and
the matrix is a two-dimensional structure whose axes are discovered at runtime —
strips are registered by whichever modules are loaded, buses are created by the
user. Encoding that as a JSON string in a setting would put a schema inside a
value, and `GET /api/settings` hands the whole bag to every plugin. It gets its
own table, the way layouts do.

**Why the whole document, not rows per cell.** A matrix read is always a whole
read (the mixer pane renders every cell) and a write is always a whole write (the
frontend owns the graph and sends its state back). Row-per-cell would buy
nothing and add a consistency problem: a half-applied matrix is a routing the
user never asked for, silently sending their microphone somewhere.

**Strips outlive their modules.** A strip's settings are kept even when no module
registers it — uninstall the karaoke module and its fader position survives, so
reinstalling does not silently reset a routing the user built. The frontend
reconciles: declared strips missing from the saved state get defaults, saved
strips nobody declared stay on disk and out of the graph.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

logger = logging.getLogger(__name__)

#: One row, this key. There is exactly one mixer per node — it models the
#: machine's audio hardware, which does not vary by workspace.
_STATE_KEY = "default"

#: Bumped when the shape changes in a way a saved document cannot be read as.
#: Loading a newer document than we understand returns defaults rather than
#: guessing, because a misread matrix routes audio somewhere the user did not ask.
SCHEMA_VERSION = 1

_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked before the call: `init_audio_db` reaches this helper again.
        _initialized.add(path)
        init_audio_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_audio_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audio_state (
                key        TEXT PRIMARY KEY,
                version    INTEGER NOT NULL,
                document   TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


def default_state() -> dict[str, Any]:
    """A fresh mixer: one bus on the system default output, nothing routed away.

    The default deliberately reproduces the behaviour of an app with no mixer at
    all — everything to one output, no cells flipped. Installing this feature
    must not change what a user hears until they ask it to.
    """
    return {
        "version": SCHEMA_VERSION,
        "buses": [
            {
                "id": "A1",
                "label": "Main",
                "deviceId": "",
                "deviceLabel": "",
                "gain": 0.0,
                "muted": False,
                "virtual": False,
            }
        ],
        "strips": [],
        "inputDeviceId": "",
        "inputDeviceLabel": "",
    }


def load_state() -> dict[str, Any]:
    """Read the saved matrix, or defaults.

    A document from a *newer* schema is discarded rather than partially read.
    See the module docstring — a half-understood matrix is not a safe fallback.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT version, document FROM audio_state WHERE key = ?", (_STATE_KEY,)
        ).fetchone()
    if row is None:
        return default_state()
    if int(row["version"]) > SCHEMA_VERSION:
        logger.warning(
            "audio: saved mixer state is version %s, this build understands %s; using defaults",
            row["version"],
            SCHEMA_VERSION,
        )
        return default_state()
    try:
        document = json.loads(row["document"])
    except json.JSONDecodeError:
        logger.warning("audio: saved mixer state is not valid JSON; using defaults")
        return default_state()
    if not isinstance(document, dict):
        return default_state()
    document.setdefault("version", SCHEMA_VERSION)
    return document


def save_state(document: dict[str, Any]) -> dict[str, Any]:
    """Replace the saved matrix. Returns what was stored."""
    stored = dict(document)
    stored["version"] = SCHEMA_VERSION
    payload = json.dumps(stored)
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO audio_state (key, version, document, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                version = excluded.version,
                document = excluded.document,
                updated_at = excluded.updated_at
            """,
            (_STATE_KEY, SCHEMA_VERSION, payload),
        )
    return stored


def reset_state() -> dict[str, Any]:
    """Forget the saved matrix and return to defaults."""
    with get_db_conn() as conn:
        conn.execute("DELETE FROM audio_state WHERE key = ?", (_STATE_KEY,))
    return default_state()
