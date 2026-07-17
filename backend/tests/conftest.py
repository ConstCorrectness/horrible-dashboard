import pytest


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # The secrets master key lives outside the data dir by design (see
    # secrets_store.get_key_path), so it needs isolating separately — otherwise a test
    # would read and write the developer's real ~/.horrible/secrets.key.
    monkeypatch.setenv("SECRETS_KEY_PATH", str(tmp_path / "secrets.key"))
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
