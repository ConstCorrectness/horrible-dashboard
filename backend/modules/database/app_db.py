"""The path to the app's own SQLite database.

`$HORRIBLE_DATA_DIR/app.db` is the node's local relational store — `library_sources`,
`browser_history`, `browser_bookmarks`, `code_symbols`, `async_tasks`. Every module
that keeps a table here goes through this function.

**This is not the vector store.** `vectorstore.get_db_path()` returns
`$HORRIBLE_DATA_DIR/lancedb`, a LanceDB *directory*, and the two are easy to confuse:
before the LanceDB migration the vector store *was* a SQLite file, so a single
`get_db_path()` meant both. It doesn't any more — passing the LanceDB directory to
`sqlite3.connect()` fails with the less-than-obvious "unable to open database file",
which is exactly what the built-in `app` connection did until this function existed.
Keep the two names apart.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def get_app_db_path() -> Path:
    """The app database file. May not exist yet — SQLite creates it on connect, as
    long as the parent directory is there (see `ensure_app_db_dir`)."""
    return get_data_dir() / "app.db"


def ensure_app_db_dir() -> Path:
    """Create the data dir if needed and return the app DB path. Call before
    connecting: SQLite creates a missing *file*, but not a missing *directory*."""
    path = get_app_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
