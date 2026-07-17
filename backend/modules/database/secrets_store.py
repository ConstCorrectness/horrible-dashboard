"""Encrypted at-rest storage for credentials (API keys, OAuth tokens).

A Fernet-encrypted SQLite table at `$HORRIBLE_DATA_DIR/secrets.db`, keyed by an
opaque `provider_name` (the connectors module namespaces its records as
`connector:<id>`).

**What the encryption is worth.** The master key lives on the same machine as the
database, so this is *not* a defense against code running as you — anything that can
read the DB can read the key. What it does defend is the realistic accident: the data
dir being copied somewhere it shouldn't (a cloud-synced folder, a backup, `git add
.data`, a screen share). That's why the key is deliberately kept *out* of the data dir
and out of the repo's `.env`.
"""

import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

# Picks up a SECRETS_MASTER_KEY that older installs wrote into .env, so their
# existing secrets keep decrypting after the key moved to its own file.
load_dotenv()


class SecretDecryptError(Exception):
    """A stored secret exists but could not be decrypted (rotated or corrupted key).

    Distinct from "no such secret" on purpose: conflating the two makes a broken
    credential look like an unconfigured one, so the UI offers "Connect" and silently
    re-authenticates instead of saying what's actually wrong.
    """


def get_db_path() -> Path:
    return _data_dir() / "secrets.db"


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def get_key_path() -> Path:
    """Where the generated master key is persisted: `SECRETS_KEY_PATH`, else
    `~/.horrible/secrets.key`.

    Deliberately NOT in `$HORRIBLE_DATA_DIR` — storing the key beside the database it
    decrypts would defeat the one threat this design actually covers (the data dir
    being copied somewhere it shouldn't). The env override exists so tests (and
    multi-node dev setups) can isolate the key the way they isolate the data dir.
    """
    if override := os.environ.get("SECRETS_KEY_PATH"):
        return Path(override)
    return Path.home() / ".horrible" / "secrets.key"


def _restrict_permissions(path: Path) -> None:
    """Best-effort owner-only file mode. A no-op on Windows, where chmod only maps to
    the read-only flag — the parent directory ACL is the real control there."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass


def get_master_key() -> bytes:
    """The Fernet key: `SECRETS_MASTER_KEY` if set, else a persisted per-install
    random key. Generated once on first use and written owner-only."""
    key = os.environ.get("SECRETS_MASTER_KEY")
    if key:
        return key.encode("utf-8")

    path = get_key_path()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip().encode("utf-8")

    generated = Fernet.generate_key().decode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated, encoding="utf-8")
    _restrict_permissions(path)
    return generated.encode("utf-8")


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                provider_name TEXT PRIMARY KEY,
                encrypted_key BLOB NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def upsert_secret(provider_name: str, secret_value: str) -> None:
    fernet = Fernet(get_master_key())
    encrypted_key = fernet.encrypt(secret_value.encode("utf-8"))

    init_db()
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO secrets (provider_name, encrypted_key, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(provider_name) DO UPDATE SET
                encrypted_key = excluded.encrypted_key,
                updated_at = CURRENT_TIMESTAMP
            """,
            (provider_name, encrypted_key),
        )


def _read_encrypted(provider_name: str) -> bytes | None:
    init_db()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT encrypted_key FROM secrets WHERE provider_name = ?",
            (provider_name,),
        ).fetchone()
    return row["encrypted_key"] if row is not None else None


def get_secret(provider_name: str) -> str | None:
    """The stored secret, or None if it was never set.

    Raises `SecretDecryptError` if a record exists but won't decrypt, so callers can
    tell "broken" from "absent". Use `get_secret_or_none` if you only care about the
    happy path.
    """
    encrypted = _read_encrypted(provider_name)
    if encrypted is None:
        return None
    try:
        return Fernet(get_master_key()).decrypt(encrypted).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptError(
            f"stored secret {provider_name!r} could not be decrypted — the master key "
            "has changed or the record is corrupted; delete and re-enter it"
        ) from exc


def get_secret_or_none(provider_name: str) -> str | None:
    """`get_secret`, but an undecryptable record reads as absent. For callers that
    have no way to surface the difference."""
    try:
        return get_secret(provider_name)
    except SecretDecryptError:
        return None


def delete_secret(provider_name: str) -> bool:
    init_db()
    with get_db_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM secrets WHERE provider_name = ?", (provider_name,)
        )
        return cursor.rowcount > 0


def list_providers() -> list[str]:
    init_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT provider_name FROM secrets ORDER BY provider_name"
        ).fetchall()
        return [r["provider_name"] for r in rows]
