"""The authoring HTTP surface, asserted on response bodies.

Every assertion here reads the JSON the browser would receive, never a helper's return
value. These routes declare `response_model`s, and a Pydantic response model silently
drops any field it doesn't declare — a test reading the helper passes while the pane
renders `undefined` and nothing anywhere raises. `inputSchema` on a tool is exactly
that shape of field: newly added, carried only for the browser, and invisible to the
backend if it goes missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.modules.mcp import author
from backend.modules.mcp import config as cfg

FIXTURE_SERVER = str(Path(__file__).parent / "mcp_fixture_server.py")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    author._projects.clear()
    return tmp_path


@pytest.fixture
def client(data_dir: Path):
    """A client entered as a context manager, which matters more than it looks.

    An un-entered `TestClient` runs each request in its own event loop. An MCP session
    is a long-lived supervisor task pinned to the loop that started it, so the server
    would connect during one request and be unreachable — reporting itself `ready`
    while every call answered "not connected" — in the next. Entering the client keeps
    one loop for the client's lifetime, which is what the real app has.
    """
    from fastapi.testclient import TestClient

    from backend.app import app

    with TestClient(app) as entered:
        yield entered


@pytest.fixture
def connected(client, data_dir: Path):
    """The fixture server, added and connected through the real routes."""
    body = client.post(
        "/api/mcp/servers",
        json={
            "id": "fixture",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
        },
    ).json()
    assert body["state"] == "ready", body.get("error")
    yield body
    client.delete("/api/mcp/servers/fixture")


# --- what a server row carries ------------------------------------------------


def test_a_tools_schema_reaches_the_browser(connected):
    """Without it the invoke form has nothing to generate from, and the failure is a
    blank form rather than an error."""
    peek = next(t for t in connected["tools"] if t["name"] == "peek")
    assert peek["inputSchema"]["type"] == "object"
    assert "key" in peek["inputSchema"]["properties"]
    assert peek["inputSchema"]["required"] == ["key"]


def test_provenance_and_protocol_version_reach_the_browser(connected):
    assert connected["origin"] == "manual"
    assert connected["protocolVersion"]


def test_a_registry_add_is_labelled_as_third_party(client, data_dir: Path):
    """The one moment provenance is knowable is when it's added; after that the label
    is the only record that this is somebody else's code."""
    body = client.post(
        "/api/mcp/servers",
        json={
            "id": "third",
            "transport": "stdio",
            "command": sys.executable,
            "args": [FIXTURE_SERVER],
            "origin": "registry",
        },
    ).json()
    assert body["origin"] == "registry"
    client.delete("/api/mcp/servers/third")


def test_an_unrecognized_origin_falls_back_to_manual(data_dir: Path):
    """Not `authored`: an origin we don't recognize is not evidence the code is ours."""
    stored = cfg.save_server(
        {"id": "x", "transport": "stdio", "command": "echo", "origin": "trustworthy"}
    )
    assert stored["origin"] == "manual"


# --- invoking a tool by hand --------------------------------------------------


def test_a_tool_can_be_run_from_the_pane(client, connected):
    body = client.post(
        "/api/mcp/servers/fixture/call",
        json={"name": "peek", "arguments": {"key": "k"}},
    ).json()
    assert body["error"] is None
    assert body["content"] == "value:k"
    assert body["elapsedMs"] >= 0


def test_a_failing_tool_reports_its_error_rather_than_500ing(client, connected):
    """The result is the thing being inspected — a route that raised would hide the
    exact payload the model would have received."""
    response = client.post(
        "/api/mcp/servers/fixture/call", json={"name": "boom", "arguments": {}}
    )
    assert response.status_code == 200
    assert response.json()["error"]


def test_calling_a_disconnected_server_is_a_409(client, data_dir: Path):
    """409, not 404: the difference between "connect it" and "it doesn't exist" is the
    difference between two entirely different things for the pane to say."""
    cfg.save_server({"id": "off", "transport": "stdio", "command": "echo"})
    response = client.post(
        "/api/mcp/servers/off/call", json={"name": "x", "arguments": {}}
    )
    assert response.status_code == 409


# --- conformance --------------------------------------------------------------


def test_the_conformance_report_reaches_the_browser_whole(client, connected):
    body = client.post("/api/mcp/servers/fixture/conformance").json()
    assert body["status"] in ("pass", "warn")
    ids = {c["id"] for c in body["checks"]}
    assert {"handshake", "capabilities", "schemas", "annotations"} <= ids
    # A check with a status but no detail would render as an unexplained verdict.
    assert all(c["title"] for c in body["checks"])


# --- projects -----------------------------------------------------------------


def test_scaffolding_through_the_route_returns_the_project(client, data_dir: Path):
    body = client.post(
        "/api/mcp/projects", json={"id": "mine", "template": "python", "title": "Mine"}
    ).json()
    assert body["id"] == "mine"
    assert body["entry"] == "server.py"
    assert "server.py" in body["files"]
    assert body["state"] == "new"


def test_a_scaffolded_project_appears_as_a_disabled_server(client, data_dir: Path):
    client.post("/api/mcp/projects", json={"id": "mine", "template": "python"})
    servers = client.get("/api/mcp/servers").json()["servers"]
    row = next(s for s in servers if s["id"] == "mine")
    assert row["origin"] == "authored"
    assert row["project"] == "mine"
    assert row["enabled"] is False


def test_a_duplicate_project_id_is_a_400_not_a_500(client, data_dir: Path):
    client.post("/api/mcp/projects", json={"id": "mine", "template": "python"})
    response = client.post(
        "/api/mcp/projects", json={"id": "mine", "template": "python"}
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_reading_and_writing_a_project_file(client, data_dir: Path):
    client.post("/api/mcp/projects", json={"id": "mine", "template": "python"})
    original = client.get(
        "/api/mcp/projects/mine/file", params={"path": "server.py"}
    ).json()["text"]
    assert "FastMCP" in original

    # `restart: false` because there is nothing running to restart — the project has
    # not been provisioned, and the route must not treat that as a failure.
    written = client.post(
        "/api/mcp/projects/mine/file",
        json={"path": "README.md", "text": "# edited\n", "restart": False},
    ).json()
    assert written["restarted"] is False
    assert (
        client.get("/api/mcp/projects/mine/file", params={"path": "README.md"}).json()[
            "text"
        ]
        == "# edited\n"
    )


def test_a_path_escaping_the_project_is_a_400(client, data_dir: Path):
    client.post("/api/mcp/projects", json={"id": "mine", "template": "python"})
    response = client.get(
        "/api/mcp/projects/mine/file", params={"path": "../../secrets.json"}
    )
    assert response.status_code == 400


def test_removing_a_project_keeps_its_source(client, data_dir: Path):
    """ "Remove" on a list row is not consent to delete a source tree."""
    created = client.post(
        "/api/mcp/projects", json={"id": "mine", "template": "python"}
    ).json()
    body = client.delete("/api/mcp/projects/mine").json()
    assert Path(created["root"], "server.py").is_file()
    assert client.get("/api/mcp/servers").json()["servers"] == []
    # The directory is still there, so the row is too — marked unregistered rather
    # than hidden, or scaffolding the same id again fails for a reason nothing shows.
    assert [p["registered"] for p in body["projects"]] == [False]


def test_an_unregistered_project_can_be_added_back(client, data_dir: Path):
    client.post("/api/mcp/projects", json={"id": "mine", "template": "python"})
    client.delete("/api/mcp/projects/mine")
    body = client.post("/api/mcp/projects/mine/register").json()
    assert body["registered"] is True
    assert [s["id"] for s in client.get("/api/mcp/servers").json()["servers"]] == [
        "mine"
    ]


def test_deleting_the_files_is_possible_but_asked_for(client, data_dir: Path):
    created = client.post(
        "/api/mcp/projects", json={"id": "mine", "template": "python"}
    ).json()
    client.delete("/api/mcp/projects/mine", params={"deleteFiles": True})
    assert not Path(created["root"]).exists()


def test_the_project_list_reports_the_machine_s_toolchains(client, data_dir: Path):
    """So a missing toolchain is named before a project is scaffolded that nothing
    here can build."""
    body = client.get("/api/mcp/projects").json()
    assert isinstance(body["hasUv"], bool)
    assert isinstance(body["hasNpm"], bool)
