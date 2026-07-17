"""Credential custody: key resolution, encryption round-trip, and the two failure
modes that used to be silent (the key landing in `.env`, and an undecryptable record
reading as "not configured")."""

import os
import stat
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from backend.modules.database import secrets_store
from backend.modules.database.secrets_store import (
    SecretDecryptError,
    delete_secret,
    get_key_path,
    get_master_key,
    get_secret,
    get_secret_or_none,
    list_providers,
    upsert_secret,
)


def test_round_trip():
    upsert_secret("github", "ghp_token")
    assert get_secret("github") == "ghp_token"


def test_absent_secret_is_none():
    assert get_secret("never-set") is None


def test_upsert_overwrites():
    upsert_secret("github", "first")
    upsert_secret("github", "second")
    assert get_secret("github") == "second"


def test_delete_and_list():
    upsert_secret("a", "1")
    upsert_secret("b", "2")
    assert list_providers() == ["a", "b"]
    assert delete_secret("a") is True
    assert delete_secret("a") is False
    assert list_providers() == ["b"]


def test_value_is_encrypted_at_rest(tmp_path):
    """The point of the whole module: the plaintext must not be sitting in the file."""
    upsert_secret("github", "super-secret-value")
    blob = (tmp_path / "secrets.db").read_bytes()
    assert b"super-secret-value" not in blob


def test_env_key_wins(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SECRETS_MASTER_KEY", key)
    assert get_master_key() == key.encode()
    # An env key means nothing is persisted to disk.
    assert not get_key_path().exists()


def test_generated_key_is_persisted_and_reused():
    first = get_master_key()
    assert get_key_path().is_file()
    assert get_master_key() == first, "a second call must reuse the persisted key"


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")
def test_generated_key_is_owner_only():
    get_master_key()
    mode = stat.S_IMODE(get_key_path().stat().st_mode)
    assert mode == 0o600


def test_key_is_not_written_to_dotenv(tmp_path, monkeypatch):
    """Regression: `get_master_key` used to generate a key and append it to the repo's
    `.env`. A library function must not write to the developer's project."""
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")

    get_master_key()

    assert env.read_text(encoding="utf-8") == "EXISTING=1\n"
    assert "SECRETS_MASTER_KEY" not in os.environ


def test_default_key_path_is_not_beside_the_database(monkeypatch):
    """The default key location must live outside HORRIBLE_DATA_DIR — co-locating the
    key with the database it decrypts would defeat the threat this design covers (the
    data dir being copied somewhere it shouldn't).

    Checks the path computation rather than generating a key, because the conftest
    deliberately points SECRETS_KEY_PATH *into* tmp_path for isolation, and the real
    default would write to the developer's home directory.
    """
    monkeypatch.delenv("SECRETS_KEY_PATH", raising=False)
    key_path = get_key_path()
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"]).resolve()
    assert data_dir not in key_path.resolve().parents
    assert key_path.parent == Path.home() / ".horrible"


def test_key_path_env_override_is_honored(tmp_path):
    """What the conftest relies on to keep tests off the real home directory."""
    assert get_key_path() == Path(os.environ["SECRETS_KEY_PATH"])
    get_master_key()
    assert (tmp_path / "secrets.key").is_file()


def test_wrong_key_raises_rather_than_reading_as_absent(monkeypatch):
    """The sharp one: a rotated key used to be indistinguishable from "never set", so
    the UI offered "Connect" and silently re-authenticated."""
    monkeypatch.setenv("SECRETS_MASTER_KEY", Fernet.generate_key().decode())
    upsert_secret("github", "token")

    monkeypatch.setenv("SECRETS_MASTER_KEY", Fernet.generate_key().decode())
    with pytest.raises(SecretDecryptError):
        get_secret("github")


def test_get_secret_or_none_stays_lenient(monkeypatch):
    """Callers with no way to surface the difference keep the old behavior."""
    monkeypatch.setenv("SECRETS_MASTER_KEY", Fernet.generate_key().decode())
    upsert_secret("github", "token")

    monkeypatch.setenv("SECRETS_MASTER_KEY", Fernet.generate_key().decode())
    assert get_secret_or_none("github") is None


def test_corrupted_record_raises():
    upsert_secret("github", "token")
    with secrets_store.get_db_conn() as conn:
        conn.execute(
            "UPDATE secrets SET encrypted_key = ? WHERE provider_name = ?",
            (b"not-a-fernet-token", "github"),
        )
    with pytest.raises(SecretDecryptError):
        get_secret("github")
