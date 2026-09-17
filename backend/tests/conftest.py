import os

import pytest

#: Every variable from which `backend/atlas.py` or the database module can build a
#: connection to the shared cluster.
ATLAS_ENV_VARS = (
    "ATLAS_DB_URI",
    "ATLAS_DB_USER",
    "ATLAS_DB_PASS",
    "ATLAS_CLUSTER_HOST",
    "ATLAS_ADMIN",
    "ATLAS_ADMIN_URI",
)


def pytest_configure(config):
    """Keep the test suite off the real Atlas cluster, whatever `.env` says.

    Found on 2026-09-16: the shared `presence` directory held dozens of records
    named after this machine, each under a different person id. Every test that
    enters `with TestClient(app)` runs the full lifespan, which publishes presence
    (`network/setup.py` → `social/directory.publish`) — and `backend/__init__.py`
    loads `.env`, so the credentials were there. Each test's temp data dir minted a
    fresh person key, so each run left a new record in a directory other people's
    nodes read.

    Set to **empty strings, before anything imports `backend`**: `_load_dotenv` never
    overrides a variable that is already present, so an empty one beats the file,
    and every `atlas` reader treats blank as "not configured". Here rather than only
    in a fixture because collection imports the app, session fixtures and
    subprocesses a test spawns all run outside any per-test monkeypatch. A test that
    needs credentials sets fake ones itself (see `test_database_mongo.py`).
    """
    for name in ATLAS_ENV_VARS:
        os.environ[name] = ""
    # The relay is on by default and hosted by the game server; a test that boots the
    # app must not open a connection to it (see `network.setup.relay_url`).
    os.environ["HORRIBLE_RELAY_URL"] = "off"


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Again per test, in case one restored the environment wholesale: nothing in the
    # suite may reach the shared cluster (see `pytest_configure`).
    for name in ATLAS_ENV_VARS:
        monkeypatch.setenv(name, "")
    # The secrets master key lives outside the data dir by design (see
    # secrets_store.get_key_path), so it needs isolating separately — otherwise a test
    # would read and write the developer's real ~/.horrible/secrets.key.
    monkeypatch.setenv("SECRETS_KEY_PATH", str(tmp_path / "secrets.key"))
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    # The other roots (backend/paths.py) are isolated for the same reason: without
    # this, a test that touches config or cache writes into the developer's real
    # ~/.horrible. Logs are left alone — `<repo>/logs/backend.log` is where every
    # debugging note in this project says to look, including during a test run.
    # Siblings of tmp_path, never children: config holds the master key, and
    # nesting it inside the data dir is the one arrangement `paths.config_dir()`
    # exists to avoid. Isolation that breaks the invariant it is isolating would
    # make `test_default_key_path_is_not_beside_the_database` pass on a lie.
    monkeypatch.setenv(
        "HORRIBLE_CONFIG_DIR", str(tmp_path.with_name(f"{tmp_path.name}-config"))
    )
    monkeypatch.setenv(
        "HORRIBLE_CACHE_DIR", str(tmp_path.with_name(f"{tmp_path.name}-cache"))
    )


@pytest.fixture(autouse=True)
def reset_process_global_stores():
    """Clear the caches that outlive `isolate_data_dir`.

    `monkeypatch.setenv("HORRIBLE_DATA_DIR", ...)` isolates everything that reads
    the environment *per call*. It does nothing for a **process-global singleton
    holding state in memory**, which survives every test in the run — so one
    test's data is visible to all the tests after it, and the failure surfaces
    somewhere else entirely as an ordering-dependent flake.

    That is exactly how `test_bios_reach_the_prompt_when_known` failed: an earlier
    test in `test_clubhouse_voice_routes.py` legitimately exercised the people
    store with a member named Ada carrying no bio, and `voice.render_bios`
    deliberately prefers learned memory over a static bio — so the later test's
    room, whose Ada *does* have a bio, rendered the remembered line instead.

    Imported lazily and only when already loaded: a test that never touches
    clubhouse should not pay for importing it, and importing modules from a
    blanket autouse fixture is how a fast suite becomes a slow one.
    """
    import sys

    yield

    memory = sys.modules.get("backend.modules.clubhouse.people_memory")
    if memory is not None:
        memory.people_memory_store.reset()

    # The port learned from the first request. `TestClient` reports its fake server
    # as `("testserver", 80)`, so without this every test after one that drove the
    # app saw port 80 in invites, presence records and the OAuth redirect.
    server_port = sys.modules.get("backend.server_port")
    if server_port is not None:
        server_port.reset()
