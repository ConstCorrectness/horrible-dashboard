"""The module end to end: create a suite, sweep it, read the scoreboard.

Uses the scripted model from the runner tests, so this exercises the routes, the
store, the sweep orchestration and the grader together — everything except a real
provider.
"""

from __future__ import annotations

import json


import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import providers as P
from backend.modules.evals import store, sweep
from backend.tests.test_evals_runner import ScriptedModel, tool_decl


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A node whose data dir is empty, so suites and results start from nothing."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Both caches are keyed by path, but the module-level sets survive the env
    # change, so clear them or the tables land in the previous test's database.
    store._initialized.clear()
    return TestClient(app)


@pytest.fixture
def connected(monkeypatch):
    """A stand-in browser on the socket registry, carrying a tool manifest.

    A sweep needs one: the frontend tool catalog only exists on a live connection,
    and without it every UI-shaped case would be graded against a catalog that does
    not contain the tools it names.
    """
    from backend.modules import ws

    class FakeConn:
        agent_tools = [tool_decl("ui.noop"), tool_decl("library.search")]

        async def send_json(self, data):  # pragma: no cover - never called here
            pass

    conn = FakeConn()
    ws._active_connections.add(conn)
    yield conn
    ws._active_connections.discard(conn)


def _forked_starter(client) -> str:
    """A writable copy of the bundled starter suite, for tests that need cases."""
    suites = client.get("/api/evals/suites").json()["suites"]
    starter = next(s for s in suites if s["source"] == "bundled")
    return client.post(f"/api/evals/suites/{starter['id']}/fork", json={}).json()["id"]


def test_the_starter_suite_is_bundled_data_not_code(client):
    """The cases ship as a `.jsonl` in the repo, in the same format you author in.

    They used to be Python constructors compiled into the backend, which gave the
    module two authoring formats — one for us and one for everybody else. This is
    the `hassault` rule: bundled content is a data file, resolved beside yours.
    """
    suites = client.get("/api/evals/suites").json()["suites"]
    starter = next(s for s in suites if s["source"] == "bundled")

    assert starter["id"].startswith("bundled:")
    assert starter["read_only"] is True
    assert starter["path"].endswith(".jsonl")
    # It is in the repo, not the data dir — that is what makes it reviewable.
    assert "backend" in starter["path"]

    body = client.get(f"/api/evals/suites/{starter['id']}/cases").json()
    assert not body["error"]
    grades = {c["expect"]["grade"] for c in body["cases"]}
    # The starter set has to include negatives, or it would score a model that
    # calls tools at everything as excellent.
    assert "no_call" in grades


def test_a_new_suite_is_empty(client):
    """No silent seeding. A new suite is yours and starts blank; fork the bundled
    one if you want its cases."""
    suite = client.post("/api/evals/suites", json={"name": "Mine"}).json()
    assert suite["case_count"] == 0
    assert suite["source"] == "user"
    assert suite["read_only"] is False


def test_a_bundled_suite_cannot_be_written_to(client):
    """Writing would edit a file inside the repo — lost on the next pull, and a
    dirty working tree in the meantime."""
    suites = client.get("/api/evals/suites").json()["suites"]
    starter = next(s for s in suites if s["source"] == "bundled")

    r = client.put(f"/api/evals/suites/{starter['id']}/cases", json=[])
    assert r.status_code == 409
    assert "fork" in r.json()["detail"].lower()

    assert client.delete(f"/api/evals/suites/{starter['id']}").status_code == 409


def test_forking_a_bundled_suite_gives_you_an_editable_copy(client):
    suites = client.get("/api/evals/suites").json()["suites"]
    starter = next(s for s in suites if s["source"] == "bundled")

    fork = client.post(
        f"/api/evals/suites/{starter['id']}/fork", json={"name": "My cases"}
    ).json()
    assert fork["source"] == "user"
    assert fork["read_only"] is False
    assert fork["case_count"] == starter["case_count"]
    # And it is writable, which is the whole point of forking.
    assert (
        client.put(f"/api/evals/suites/{fork['id']}/cases", json=[]).status_code == 200
    )


def test_a_user_suite_id_can_never_collide_with_a_bundled_one(client):
    """Bundled ids are prefixed and user ids are 12 hex characters, so neither
    catalog can shadow the other — the `hd_*` map rule."""
    suite = client.post("/api/evals/suites", json={"name": "bundled:starter"}).json()
    assert not suite["id"].startswith("bundled:")

    suites = client.get("/api/evals/suites").json()["suites"]
    ids = [s["id"] for s in suites]
    assert len(ids) == len(set(ids))


def test_a_bundled_id_cannot_be_used_to_read_an_arbitrary_file(client):
    """An id arrives from a URL, so `bundled:../../secrets` must not become a path.
    Only a slug the catalog knows resolves to anything at all."""
    for evil in ("bundled:../../../etc/passwd", "bundled:nonesuch"):
        assert client.get(f"/api/evals/suites/{evil}/cases").status_code == 404


def test_a_broken_case_file_reports_the_line_rather_than_404ing(client):
    """The suite exists; it is the JSON that is broken. A 4xx would leave the pane
    with nothing to render but a toast, so the parse error rides on a 200."""
    suite = client.post(
        "/api/evals/suites", json={"name": "Broken", "seed": False}
    ).json()
    from pathlib import Path

    Path(suite["path"]).write_text(
        '{"id": "ok", "prompt": "hi"}\nnot json\n', encoding="utf-8"
    )

    body = client.get(f"/api/evals/suites/{suite['id']}/cases").json()
    assert body["cases"] == []
    assert "line 2" in body["error"]


def test_duplicate_case_ids_are_refused(client):
    """Results are keyed by (run, case), so a duplicate id would silently overwrite
    the first row and the suite would be one case shorter than it looks."""
    suite = client.post(
        "/api/evals/suites", json={"name": "Dupes", "seed": False}
    ).json()
    from pathlib import Path

    Path(suite["path"]).write_text(
        '{"id": "a", "prompt": "one"}\n{"id": "a", "prompt": "two"}\n', encoding="utf-8"
    )
    body = client.get(f"/api/evals/suites/{suite['id']}/cases").json()
    assert "duplicate case id" in body["error"]


def test_a_sweep_with_no_browser_attached_refuses_with_a_reason(client):
    """Rather than grading every model against an empty catalog and reporting a
    row of zeros that looks like a model problem."""
    suite_id = _forked_starter(client)
    body = client.post(
        "/api/evals/runs",
        json={"suite_id": suite_id, "targets": [{"model": "m"}]},
    ).json()
    assert body["started"] is False
    assert "browser" in body["message"].lower()


def test_a_sweep_needs_at_least_one_target(client, connected):
    suite_id = _forked_starter(client)
    assert (
        client.post(
            "/api/evals/runs", json={"suite_id": suite_id, "targets": []}
        ).status_code
        == 422
    )


@pytest.mark.anyio
async def test_a_sweep_runs_and_the_scoreboard_reads_back(
    client, connected, monkeypatch
):
    """The whole loop: suite → sweep → per-case rows → run totals."""
    monkeypatch.setattr(
        P,
        "chat_stream",
        ScriptedModel(["I do not need a tool for that."] * 40),
    )

    suite_id = _forked_starter(client)
    runs = await sweep.run_sweep(
        suite_id,
        [
            type(
                "T",
                (),
                {
                    "provider": "ollama",
                    "endpoint": "http://localhost:11434",
                    "model": "scripted",
                    "label": "scripted",
                    "temperature": 0.0,
                },
            )()
        ],
        connected.agent_tools,
    )

    assert len(runs) == 1
    run = runs[0]
    assert run.status == "done"
    assert run.completed == run.total

    # A model that never calls a tool passes exactly the negative cases. That is the
    # point of having them: without negatives this model would score zero and look
    # broken, and with only negatives it would score full marks.
    assert 0 < run.passed < run.total

    body = client.get(f"/api/evals/runs/{run.id}").json()
    assert len(body["results"]) == run.total
    failures = [r for r in body["results"] if not r["passed"]]
    assert failures and all(r["detail"] for r in failures), (
        "every failing row must say why; a pass rate you cannot explain is useless"
    )


@pytest.mark.anyio
async def test_a_target_that_cannot_be_resolved_fails_as_a_run(client, connected):
    """ "Your endpoint is wrong" and "this model gets everything wrong" look
    identical on a scoreboard unless the run itself carries the error."""
    suite_id = _forked_starter(client)
    runs = await sweep.run_sweep(
        suite_id,
        [
            type(
                "T",
                (),
                {
                    "provider": "nonesuch",
                    "endpoint": "",
                    "model": "m",
                    "label": "",
                    "temperature": None,
                },
            )()
        ],
        connected.agent_tools,
    )
    assert runs[0].status == "failed"
    assert "nonesuch" in runs[0].error


def test_the_agent_tools_are_grouped_under_their_own_prefix():
    """An omitted `group=` charges the tool's schema to every round of every agent.
    The prefix and the declared group must also match, or the guide never loads."""
    from backend.modules.evals.agent_tools import _TOOLS

    for tool in _TOOLS:
        assert tool.group == "evals", f"{tool.name} declares group {tool.group!r}"
        assert tool.name.split(".")[0] == "evals"


def test_the_evals_group_has_a_description():
    """Without one it shows up in `list_tool_groups` as a bare name, and a model
    has no reason to load it."""
    from backend.modules.agent.orchestrator import _group_description

    assert "evaluation" in _group_description("evals").lower()


def test_an_unknown_field_in_a_case_is_an_error_not_silence(client):
    """Pydantic ignores unknown keys by default, which for hand-authored JSONL is
    the worst behaviour available: `target_regexp` for `target_regex` silently does
    nothing, the run scores zero, and the obvious conclusion is that the feature is
    broken rather than that the key is misspelled.

    This is a regression test for a real incident — a case written against a newer
    schema than the running backend had two fields dropped on the way in, and the
    only symptom was a benchmark scoring 0.000.
    """
    suite = client.post("/api/evals/suites", json={"name": "Strict"}).json()
    r = client.put(
        f"/api/evals/suites/{suite['id']}/cases",
        json=[
            {"id": "typo", "prompt": "p", "expect": {"grade": "no_call"}, "nonsense": 1}
        ],
    )
    assert r.status_code == 422
    assert "nonsense" in json.dumps(r.json())
