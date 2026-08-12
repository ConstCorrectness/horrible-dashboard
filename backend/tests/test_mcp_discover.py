"""Discovering, inspecting and accounting for MCP servers.

Three separable things, tested at the level each one actually lives at:

- the **catalog** mapping (registry JSON → a runnable config) is pure, and is asserted
  against captured shapes from the real registry — the mapping is where the sharp
  edges are, not the HTTP;
- the **transcript** tee is asserted against the fixture server, because its whole
  claim is that wrapping the streams doesn't disturb the session;
- the **secret-env split** is asserted on the file on disk, since "the key must not be
  in the plaintext config" is a statement about that file and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.modules.mcp import catalog
from backend.modules.mcp import config as cfg
from backend.modules.mcp import transcript
from backend.modules.mcp.client import McpSession

FIXTURE_SERVER = str(Path(__file__).parent / "mcp_fixture_server.py")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


# --- catalog: ids -------------------------------------------------------------


def test_suggested_id_drops_the_reverse_dns_prefix():
    """The id becomes the `mcp-<id>.` prefix on every tool name the model reads, so
    `io_github_owner_thing` would be pure context cost for no information."""
    assert catalog.suggest_id("io.github.owner/mcp-filesystem") == "mcp-filesystem"
    assert catalog.suggest_id("com.pulsemcp/remote-filesystem") == "remote-filesystem"


def test_suggested_id_is_always_a_legal_server_id():
    for name in ("io.github.o/Weird Name!", "UPPER/CASE", "", "///"):
        suggested = catalog.suggest_id(name)
        assert cfg.validate_id(suggested) is None, suggested


# --- catalog: install options -------------------------------------------------

NPM_ENTRY = {
    "name": "com.pulsemcp/remote-filesystem",
    "description": "Cloud storage.",
    "version": "0.1.5",
    "repository": {"url": "https://github.com/pulsemcp/mcp-servers"},
    "packages": [
        {
            "registryType": "npm",
            "identifier": "remote-filesystem-mcp-server",
            "version": "0.1.5",
            "runtimeHint": "npx",
            "transport": {"type": "stdio"},
            "runtimeArguments": [{"value": "-y", "type": "positional"}],
            "environmentVariables": [
                {"name": "GCS_BUCKET", "isRequired": True},
                {"name": "GCS_PRIVATE_KEY", "isSecret": True},
            ],
        }
    ],
}


def test_npm_entry_becomes_an_npx_command():
    entry = catalog.parse_entry({"server": NPM_ENTRY})
    assert entry is not None
    option = entry.installs[0]
    assert option.command == "npx"
    assert option.args == ["-y", "remote-filesystem-mcp-server@0.1.5"]
    assert option.transport == "stdio"


def test_npx_gets_yes_added_when_the_entry_did_not_declare_it():
    """`npx` prompts before installing, and its stdin is the protocol pipe — the
    prompt is never answered and the connect times out 90 seconds later."""
    raw = json.loads(json.dumps(NPM_ENTRY))
    del raw["packages"][0]["runtimeArguments"]
    option = catalog.install_options(raw)[0]
    assert option.args[0] == "-y"


def test_npx_yes_is_not_added_twice():
    option = catalog.install_options(NPM_ENTRY)[0]
    assert option.args.count("-y") == 1


def test_a_missing_runtime_hint_is_inferred_from_the_registry_type():
    """Most pypi entries carry no `runtimeHint` at all."""
    raw = {
        "packages": [
            {
                "registryType": "pypi",
                "identifier": "vs-filesystem-mcp-server",
                "version": "0.1.3",
            }
        ]
    }
    option = catalog.install_options(raw)[0]
    assert option.command == "uvx"
    assert option.args == ["vs-filesystem-mcp-server@0.1.3"]


def test_an_unmappable_package_says_so_rather_than_inventing_a_command():
    raw = {"packages": [{"registryType": "cargo", "identifier": "thing"}]}
    option = catalog.install_options(raw)[0]
    assert option.command == ""
    assert "cargo" in option.unsupported


def test_secret_environment_variables_are_flagged():
    option = catalog.install_options(NPM_ENTRY)[0]
    by_name = {v.name: v for v in option.env}
    assert by_name["GCS_PRIVATE_KEY"].secret
    assert by_name["GCS_BUCKET"].required
    assert not by_name["GCS_BUCKET"].secret


def test_a_remote_becomes_an_http_option_and_comes_first():
    """A hosted server executes none of the publisher's code on this machine, so when
    an entry offers both it is the one to default to."""
    raw = {
        "remotes": [{"type": "streamable-http", "url": "https://server.example/mcp"}],
        "packages": [
            {"registryType": "npm", "identifier": "thing", "runtimeHint": "npx"}
        ],
    }
    options = catalog.install_options(raw)
    assert options[0].kind == "remote"
    assert options[0].transport == "http"
    assert options[0].url == "https://server.example/mcp"


def test_arguments_without_a_value_are_not_invented():
    """A declaration saying "this server takes a --root you must supply" has no value
    to pass; emitting the flag alone produces an argv that fails unreadably."""
    raw = {
        "packages": [
            {
                "registryType": "npm",
                "identifier": "thing",
                "runtimeHint": "npx",
                "packageArguments": [
                    {"type": "named", "name": "--root", "isRequired": True},
                    {"type": "named", "name": "--mode", "value": "ro"},
                    {"type": "positional", "value": "extra"},
                ],
            }
        ]
    }
    option = catalog.install_options(raw)[0]
    assert option.args == ["-y", "thing", "--mode", "ro", "extra"]


def test_parse_entry_rejects_a_row_with_no_name():
    assert catalog.parse_entry({"server": {"description": "x"}}) is None


def test_parse_entry_caps_third_party_text():
    entry = catalog.parse_entry({"server": {"name": "a/b", "description": "x" * 5000}})
    assert entry is not None
    assert len(entry.description) <= 600


# --- catalog: the overlay -----------------------------------------------------


def test_the_shipped_overlay_is_all_runnable():
    entries = catalog.curated_entries()
    assert entries, "the curated catalog should not be empty"
    for entry in entries:
        assert entry.installs, entry.name
        assert entry.installs[0].command, entry.name
        assert not entry.installs[0].unsupported, entry.name
        assert cfg.validate_id(entry.suggested_id) is None, entry.name


def test_merge_keeps_the_curated_description_over_the_registrys():
    curated = [
        catalog.CatalogEntry(name="a/b", title="Ours", description="", source="curated")
    ]
    live = [catalog.CatalogEntry(name="A/B", title="Theirs", description="")]
    merged = catalog.merge(curated, live)
    assert [e.title for e in merged] == ["Ours"]


def test_merge_keeps_registry_entries_the_overlay_does_not_cover():
    curated = [catalog.CatalogEntry(name="a/b", title="Ours", description="")]
    live = [catalog.CatalogEntry(name="c/d", title="Theirs", description="")]
    assert [e.title for e in catalog.merge(curated, live)] == ["Ours", "Theirs"]


def test_matches_searches_the_note_as_well_as_the_description():
    entry = catalog.CatalogEntry(
        name="a/b", title="T", description="", note="knowledge graph"
    )
    assert catalog.matches(entry, "knowledge")
    assert catalog.matches(entry, "")
    assert not catalog.matches(entry, "postgres")


# --- the secret-env split -----------------------------------------------------


def test_secret_env_values_never_reach_the_plaintext_config(data_dir: Path):
    """`env` is persisted in the clear, so a discovered server's API key must go to
    the encrypted store and only its *name* to the config."""
    cfg.save_server(
        {
            "id": "s",
            "transport": "stdio",
            "command": "x",
            "env": {"PUBLIC": "ok"},
            "secretEnv": ["API_KEY"],
        }
    )
    cfg.set_env_secret("s", "API_KEY", "super-secret")

    raw = (data_dir / "mcp-servers.json").read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert "API_KEY" in raw  # the name is not the secret
    assert cfg.env_secrets("s") == {"API_KEY": "super-secret"}
    assert cfg.missing_env_secrets("s") == []


def test_a_declared_secret_with_no_value_is_a_visible_state(data_dir: Path):
    """Otherwise the failure surfaces as the server's own start error, which is
    rarely legible."""
    cfg.save_server(
        {"id": "s", "transport": "stdio", "command": "x", "secretEnv": ["API_KEY"]}
    )
    assert cfg.missing_env_secrets("s") == ["API_KEY"]


def test_deleting_a_server_drops_its_environment_secrets(data_dir: Path):
    """A later server reusing the id would otherwise silently inherit them."""
    cfg.save_server(
        {"id": "s", "transport": "stdio", "command": "x", "secretEnv": ["API_KEY"]}
    )
    cfg.set_env_secret("s", "API_KEY", "v")
    assert cfg.delete_server("s")
    cfg.save_server(
        {"id": "s", "transport": "stdio", "command": "x", "secretEnv": ["API_KEY"]}
    )
    assert cfg.env_secrets("s") == {}


def test_a_config_cannot_smuggle_a_secret_value_through_save(data_dir: Path):
    cfg.save_server(
        {"id": "s", "transport": "stdio", "command": "x", "secretEnvValues": {"A": "b"}}
    )
    raw = (data_dir / "mcp-servers.json").read_text(encoding="utf-8")
    assert "secretEnvValues" not in raw


# --- the transcript -----------------------------------------------------------


def _probe(config: dict) -> tuple[McpSession, transcript.Transcript]:
    wire = transcript.Transcript()
    return McpSession(config, wire=wire), wire


@pytest.mark.timeout(120)
def test_the_tee_records_both_directions_without_disturbing_the_session(data_dir: Path):
    session, wire = _probe(
        {
            "id": "probe",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        }
    )

    async def scenario():
        await session.start()
        result = await session.call_tool("peek", {"key": "k"})
        await session.stop()
        return result

    result = asyncio.run(scenario())

    # The session still works — the whole claim of wrapping the streams.
    assert result.get("content") == "value:k"

    messages = wire.public()
    methods = [m["method"] for m in messages]
    assert "initialize" in methods
    assert "tools/call" in methods
    assert {m["direction"] for m in messages} == {"in", "out"}
    # A request and its response share an id, which is what makes the view readable
    # as a conversation rather than two unrelated columns.
    call = next(m for m in messages if m["method"] == "tools/call")
    assert any(m["direction"] == "in" and m["id"] == call["id"] for m in messages)


@pytest.mark.timeout(120)
def test_a_probe_never_touches_a_configured_servers_ring(data_dir: Path):
    # The rings are process-global and survive across tests by design, so this uses an
    # id nothing else touches rather than asserting on a shared one.
    server_id = "probe-isolation"
    session, _wire = _probe(
        {
            "id": server_id,
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        }
    )

    async def scenario():
        await session.start()
        await session.stop()

    asyncio.run(scenario())
    assert transcript.for_server(server_id).public() == []


def test_the_ring_is_bounded():
    """A long-running server would otherwise accumulate every tool result it ever
    returned, and those carry the user's own text."""
    ring = transcript.Transcript()
    for _ in range(transcript.MAX_MESSAGES + 50):
        ring.record("out", None)
    assert len(ring.public()) == transcript.MAX_MESSAGES


def test_a_huge_payload_is_truncated_and_says_so():
    class _Root:
        method = "tools/call"
        id = 1

        def model_dump_json(self, **_kwargs):
            return "x" * (transcript.MAX_PAYLOAD_CHARS * 3)

    class _Message:
        message = type("M", (), {"root": _Root()})()

    ring = transcript.Transcript()
    ring.record("out", _Message())
    row = ring.public()[0]
    assert row["truncated"]
    assert len(row["payload"]) == transcript.MAX_PAYLOAD_CHARS


def test_recording_never_raises_on_a_shape_it_does_not_understand():
    """A transcript that can break a session is worse than no transcript."""
    ring = transcript.Transcript()
    ring.record("in", object())
    ring.record("in", ValueError("bad line"))
    rows = ring.public()
    assert rows[-1]["method"] == "<parse error>"
    assert "bad line" in rows[-1]["payload"]


# --- the HTTP surface ---------------------------------------------------------
#
# Asserted on the response *body*, never on the helper's return value: these routes
# declare `response_model`s, and a Pydantic response model silently drops any field it
# doesn't declare — so a test reading the helper would pass while the browser received
# `undefined`.


@pytest.fixture
def client(data_dir: Path):
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def test_discover_serves_the_overlay_when_the_registry_is_down(client, monkeypatch):
    """A degraded list and an empty one are different answers, and the pane renders
    them differently."""

    async def no_registry(_query, *, limit=30):
        return []

    monkeypatch.setattr(catalog, "search_registry", no_registry)

    body = client.get("/api/mcp/discover").json()
    assert body["registryOnline"] is False
    assert len(body["entries"]) == len(catalog.curated_entries())
    entry = body["entries"][0]
    # Every field the pane needs has to survive the response model.
    assert entry["suggestedId"]
    assert entry["source"] == "curated"
    assert entry["installs"][0]["command"]


def test_discover_filters_the_overlay_by_the_query(client, monkeypatch):
    async def no_registry(_query, *, limit=30):
        return []

    monkeypatch.setattr(catalog, "search_registry", no_registry)

    body = client.get("/api/mcp/discover", params={"q": "knowledge graph"}).json()
    assert [e["suggestedId"] for e in body["entries"]] == ["memory"]


@pytest.mark.timeout(120)
def test_probe_reports_a_real_server_without_saving_it(client, data_dir: Path):
    body = client.post(
        "/api/mcp/probe",
        json={
            "id": "candidate",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        },
    ).json()

    assert body["ok"] is True
    assert body["serverName"] == "fixture"
    names = {t["name"]: t["readOnly"] for t in body["tools"]}
    # The annotation that decides whether the permission gate fires has to reach the
    # browser, or "inspect before you add" tells you nothing about what it can do.
    assert names["peek"] is True
    assert names["poke"] is False
    assert body["messages"], "the probe's own handshake is part of the answer"

    # Nothing persisted, nothing connected.
    assert client.get("/api/mcp/servers").json()["servers"] == []
    assert not (data_dir / "mcp-servers.json").is_file()


def test_probe_reports_a_failure_as_a_result_not_a_500(client):
    body = client.post(
        "/api/mcp/probe",
        json={
            "id": "candidate",
            "transport": "stdio",
            "command": "definitely-not-a-real-command-xyz",
        },
    ).json()
    assert body["ok"] is False
    assert "not on PATH" in (body["error"] or "")


def test_probe_rejects_an_invalid_candidate(client):
    resp = client.post("/api/mcp/probe", json={"id": "BAD ID", "transport": "stdio"})
    assert resp.status_code == 400


def test_cost_is_409_when_the_server_is_not_connected(client):
    client.post(
        "/api/mcp/servers",
        json={
            "id": "broken",
            "transport": "stdio",
            "command": "definitely-not-a-real-command-xyz",
        },
    )
    assert client.get("/api/mcp/servers/broken/cost").status_code == 409


@pytest.mark.timeout(120)
def test_cost_counts_the_serialized_schema_and_names_the_agents(client):
    created = client.post(
        "/api/mcp/servers",
        json={
            "id": "fixture",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        },
    ).json()
    assert created["state"] == "ready"

    body = client.get("/api/mcp/servers/fixture/cost").json()
    assert body["totalTokens"] == body["toolTokens"] + body["guideTokens"]
    assert body["toolTokens"] > 0
    assert {t["name"] for t in body["tools"]} >= {
        "mcp-fixture.peek",
        "mcp-fixture.poke",
    }
    # `main` is unrestricted, so it can always load an MCP group.
    assert any(a["id"] == "main" for a in body["agents"])

    wire = client.get("/api/mcp/servers/fixture/transcript").json()["messages"]
    assert any(m["method"] == "initialize" for m in wire)
    assert client.delete("/api/mcp/servers/fixture/transcript").json()["messages"] == []


def test_a_response_is_named_by_what_it_is():
    """A response carries no `method`; leaving it blank makes the view a column of
    empties exactly where the interesting half of the conversation is."""

    class _Root:
        method = None
        id = 7
        error = None

        def model_dump_json(self, **_kwargs):
            return '{"result":{}}'

    class _Message:
        message = type("M", (), {"root": _Root()})()

    ring = transcript.Transcript()
    ring.record("in", _Message())
    assert ring.public()[0]["method"] == "<result>"
