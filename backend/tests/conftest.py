import pytest


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
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
