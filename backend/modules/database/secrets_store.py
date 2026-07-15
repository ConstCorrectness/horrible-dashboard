import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()


def get_db_path() -> Path:
    data_dir = Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))
    return data_dir / "secrets.db"


def get_master_key() -> bytes:
    """Retrieve the master key from environment or generate and save a new one."""
    key = os.environ.get("SECRETS_MASTER_KEY")
    if not key:
        key = Fernet.generate_key().decode("utf-8")
        # Save to .env so it persists across restarts
        env_path = Path(".env")
        mode = "a" if env_path.exists() else "w"
        with open(env_path, mode) as f:
            f.write(f"\nSECRETS_MASTER_KEY={key}\n")
        os.environ["SECRETS_MASTER_KEY"] = key
    return key.encode("utf-8")


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


def get_secret(provider_name: str) -> str | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT encrypted_key FROM secrets WHERE provider_name = ?", (provider_name,)
        ).fetchone()

    if row is None:
        return None

    fernet = Fernet(get_master_key())
    try:
        decrypted = fernet.decrypt(row["encrypted_key"]).decode("utf-8")
        return decrypted
    except Exception:
        # Invalid key or corrupted data
        return None


def delete_secret(provider_name: str) -> bool:
    with get_db_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM secrets WHERE provider_name = ?", (provider_name,)
        )
        return cursor.rowcount > 0


def list_providers() -> list[str]:
    with get_db_conn() as conn:
        rows = conn.execute("SELECT provider_name FROM secrets ORDER BY provider_name").fetchall()
        return [r["provider_name"] for r in rows]
