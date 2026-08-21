"""Store and HTTP tests for the trajectories module.

The properties worth pinning here are the ones that fail *silently* if they break:
idempotent ingest (a retrying client doubles its own dataset), fingerprint
stability (one harness quietly becomes two and every comparison across them comes
back empty), the seal (a late step makes an export unreproducible), and payload
spill (a big tool result is the one you wanted to read).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """A node whose data dir is empty (isolated autouse by conftest).

    No lifespan: `TestClient(app)` without the context manager, matching the evals
    suite — booting the peer fabric and the research runner for a store test buys
    nothing and costs seconds.
    """
    from backend.app import app
    from backend.modules.trajectories import store

    # The init memo is keyed by path but survives the env change, so clear it or
    # the tables land in the previous test's database.
    store._initialized.clear()
    return TestClient(app)


@pytest.fixture()
def store_mod():
    from backend.modules.trajectories import store

    store._initialized.clear()
    store.init_trajectories_db()
    return store


def _write(store_mod, **overrides):
    from backend.modules.trajectories.models import (
        HarnessWrite,
        StepWrite,
        TrajectoryWrite,
    )

    body = dict(
        dataset_id="d1",
        source="external",
        goal="fix the failing test",
        status="complete",
        outcome="success",
        harness=HarnessWrite(
            agent_id="coder", model="m", provider="p", system_prompt="you are a coder"
        ),
        step_list=[
            StepWrite(kind="message", role="user", content="fix it"),
            StepWrite(
                kind="action",
                name="bash",
                args={"cmd": "pytest"},
                result={"rc": 0},
                ok=True,
            ),
        ],
    )
    body.update(overrides)
    return TrajectoryWrite(**body)


# --- fingerprinting ---------------------------------------------------------


def test_fingerprint_is_stable_across_key_order(store_mod):
    """Two clients building the same harness with differently ordered dicts must
    land on one fingerprint, or the comparison they exist for is empty."""
    from backend.modules.trajectories.models import HarnessWrite

    a = HarnessWrite(
        agent_id="c",
        model="m",
        tool_names=["b.x", "a.y"],
        tool_schemas={"a": {"p": 1, "q": 2}},
        params={"temperature": 0, "top_p": 1},
    )
    b = HarnessWrite(
        agent_id="c",
        model="m",
        tool_names=["a.y", "b.x"],
        tool_schemas={"a": {"q": 2, "p": 1}},
        params={"top_p": 1, "temperature": 0},
    )
    assert store_mod.fingerprint_harness(a) == store_mod.fingerprint_harness(b)


def test_fingerprint_ignores_label_but_tracks_prompt(store_mod):
    from backend.modules.trajectories.models import HarnessWrite

    base = HarnessWrite(agent_id="c", model="m", system_prompt="one")
    renamed = HarnessWrite(agent_id="c", model="m", system_prompt="one", label="pretty")
    changed = HarnessWrite(agent_id="c", model="m", system_prompt="two")
    assert store_mod.fingerprint_harness(base) == store_mod.fingerprint_harness(renamed)
    assert store_mod.fingerprint_harness(base) != store_mod.fingerprint_harness(changed)


def test_fingerprint_stable_in_a_fresh_process():
    """The one that rots silently: a hash that depends on anything process-local
    (dict iteration, a salt, a repr) drifts across a restart and forks the harness."""
    code = (
        "from backend.modules.trajectories.models import HarnessWrite;"
        "from backend.modules.trajectories.store import fingerprint_harness;"
        "print(fingerprint_harness(HarnessWrite(agent_id='c', model='m',"
        " system_prompt='p', tool_names=['b','a'], params={'t':0,'a':1})))"
    )
    out = [
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(2)
    ]
    assert out[0] == out[1]
    assert len(out[0]) == 16


# --- ingest -----------------------------------------------------------------


def test_ingest_creates_run_with_steps(store_mod):
    run_id, created = store_mod.ingest_run(_write(store_mod))
    assert created is True
    run = store_mod.get_run(run_id)
    assert run is not None
    assert run.goal == "fix the failing test"
    assert run.status == "complete"
    assert run.outcome == "success"
    assert run.steps == 2
    assert [s.kind for s in run.step_list] == ["message", "action"]
    action = run.step_list[1]
    assert action.name == "bash"
    assert action.args == {"cmd": "pytest"}
    assert action.result == {"rc": 0}
    assert action.ok is True
    assert run.harness_detail is not None
    assert run.harness_detail.system_prompt == "you are a coder"


def test_ingest_is_idempotent_on_external_id(store_mod):
    """A retrying client must not double its own dataset."""
    first, created_a = store_mod.ingest_run(_write(store_mod, external_id="abc"))
    second, created_b = store_mod.ingest_run(_write(store_mod, external_id="abc"))
    assert first == second
    assert created_a is True and created_b is False
    runs, total = store_mod.list_runs(dataset_id="d1")
    assert total == 1
    # Replaced, not appended: the second payload is the whole run again.
    assert runs[0].steps == 2


def test_ingest_without_external_id_files_separate_runs(store_mod):
    a, _ = store_mod.ingest_run(_write(store_mod))
    b, _ = store_mod.ingest_run(_write(store_mod))
    assert a != b
    _, total = store_mod.list_runs(dataset_id="d1")
    assert total == 2


def test_ingest_autocreates_the_dataset(store_mod):
    """A rejected trajectory is lost forever; an auto-created dataset is a row you
    can rename."""
    store_mod.ingest_run(_write(store_mod, dataset_id="never-made"))
    assert store_mod.get_dataset("never-made") is not None


def test_run_with_no_actions_is_still_a_run(store_mod):
    from backend.modules.trajectories.models import StepWrite

    run_id, _ = store_mod.ingest_run(
        _write(
            store_mod,
            step_list=[StepWrite(kind="message", role="assistant", content="no")],
        )
    )
    run = store_mod.get_run(run_id)
    assert run is not None and run.steps == 1
    assert all(s.kind != "action" for s in run.step_list)


# --- the seal ---------------------------------------------------------------


def test_sealed_run_refuses_new_steps(store_mod):
    from backend.modules.trajectories.models import StepWrite

    run_id = store_mod.start_run("d1", goal="g")
    store_mod.append_step(run_id, StepWrite(kind="action", name="a"))
    store_mod.finish_run(run_id, status="complete")
    with pytest.raises(ValueError, match="sealed"):
        store_mod.append_step(run_id, StepWrite(kind="action", name="b"))


def test_finish_run_computes_duration(store_mod):
    run_id = store_mod.start_run("d1", goal="g", started_at=1000.0)
    store_mod.finish_run(run_id, status="complete", finished_at=1002.5)
    run = store_mod.get_run(run_id)
    assert run is not None and run.duration_ms == 2500


# --- payload spill ----------------------------------------------------------


def test_large_payload_spills_to_disk_and_reads_back(store_mod):
    from backend.modules.trajectories.models import StepWrite

    big = {"blob": "x" * (store_mod.STEP_PAYLOAD_MAX + 1000)}
    run_id = store_mod.start_run("d1", goal="g")
    store_mod.append_step(run_id, StepWrite(kind="action", name="read", result=big))

    with store_mod.get_db_conn() as conn:
        raw = conn.execute(
            "SELECT result FROM traj_steps WHERE run_id = ?", (run_id,)
        ).fetchone()["result"]
    assert raw.startswith(store_mod.BLOB_PREFIX)

    run = store_mod.get_run(run_id)
    assert run is not None
    # Round-trips whole — the point of spilling rather than truncating.
    assert run.step_list[0].result == big


def test_deleting_a_run_removes_its_blobs(store_mod):
    from backend.modules.trajectories.models import StepWrite

    run_id = store_mod.start_run("d1", goal="g")
    store_mod.append_step(
        run_id,
        StepWrite(
            kind="action",
            name="r",
            result={"b": "y" * (store_mod.STEP_PAYLOAD_MAX + 10)},
        ),
    )
    directory = store_mod.blobs_dir(run_id)
    assert any(directory.iterdir())
    store_mod.delete_run(run_id)
    assert not directory.exists()


# --- redaction --------------------------------------------------------------


def test_redact_blanks_secret_shaped_keys_recursively(store_mod):
    payload = {
        "url": "https://x",
        "api_token": "sk-live-123",
        "nested": {"password": "hunter2", "keep": 1},
        "list": [{"client_secret": "s"}, {"ok": True}],
    }
    out = store_mod.redact(payload)
    assert out["url"] == "https://x"
    assert out["api_token"] == store_mod.REDACTED
    assert out["nested"]["password"] == store_mod.REDACTED
    assert out["nested"]["keep"] == 1
    assert out["list"][0]["client_secret"] == store_mod.REDACTED
    assert out["list"][1]["ok"] is True


def test_store_keeps_the_raw_value(store_mod):
    """Redaction is a boundary, not an ingest filter — the debugger needs the
    real value. This test exists so nobody "fixes" that by accident."""
    from backend.modules.trajectories.models import StepWrite

    run_id = store_mod.start_run("d1", goal="g")
    store_mod.append_step(
        run_id, StepWrite(kind="action", name="auth", args={"api_token": "sk-live-123"})
    )
    run = store_mod.get_run(run_id)
    assert run is not None
    assert run.step_list[0].args == {"api_token": "sk-live-123"}


# --- labels -----------------------------------------------------------------


def test_outcome_label_mirrors_onto_the_run(store_mod):
    from backend.modules.trajectories.models import LabelWrite

    run_id, _ = store_mod.ingest_run(_write(store_mod, outcome=None, status="complete"))
    assert store_mod.get_run(run_id).outcome is None
    store_mod.add_label(
        run_id, LabelWrite(key="outcome", value="failure", source="human")
    )
    run = store_mod.get_run(run_id)
    assert run.outcome == "failure"
    assert run.labels[0].source == "human"


def test_labels_are_additive(store_mod):
    from backend.modules.trajectories.models import LabelWrite

    run_id, _ = store_mod.ingest_run(_write(store_mod))
    store_mod.add_label(
        run_id, LabelWrite(key="outcome", value="success", source="grader")
    )
    store_mod.add_label(
        run_id, LabelWrite(key="outcome", value="failure", source="human")
    )
    run = store_mod.get_run(run_id)
    assert len(run.labels) == 2
    assert {lbl.source for lbl in run.labels} == {"grader", "human"}


# --- capture switch ---------------------------------------------------------


def test_capture_is_off_by_default(store_mod):
    store_mod.create_dataset("d1", "One")
    assert store_mod.capture_dataset_id() is None


def test_capture_returns_the_flagged_dataset(store_mod):
    store_mod.create_dataset("d1", "One")
    store_mod.create_dataset("d2", "Two", capture=True)
    assert store_mod.capture_dataset_id() == "d2"


# --- HTTP -------------------------------------------------------------------


def test_http_dataset_crud(client):
    r = client.post("/api/trajectories/datasets", json={"id": "mine", "name": "Mine"})
    assert r.status_code == 200, r.text
    assert r.json()["capture"] is False

    assert (
        client.post(
            "/api/trajectories/datasets", json={"id": "mine", "name": "Dup"}
        ).status_code
        == 409
    )

    r = client.patch("/api/trajectories/datasets/mine", json={"capture": True})
    assert r.status_code == 200 and r.json()["capture"] is True

    r = client.get("/api/trajectories/datasets")
    assert [d["id"] for d in r.json()["datasets"]] == ["mine"]


def test_http_only_one_dataset_captures(client):
    client.post(
        "/api/trajectories/datasets", json={"id": "a", "name": "A", "capture": True}
    )
    client.post("/api/trajectories/datasets", json={"id": "b", "name": "B"})
    client.patch("/api/trajectories/datasets/b", json={"capture": True})
    datasets = {
        d["id"]: d["capture"]
        for d in client.get("/api/trajectories/datasets").json()["datasets"]
    }
    assert datasets == {"a": False, "b": True}


def test_http_ingest_and_read_back(client):
    """Exercised over HTTP, not `to_dict()`: a `response_model` silently drops
    fields it does not declare, so only the real response proves the field ships."""
    body = {
        "runs": [
            {
                "dataset_id": "d1",
                "source": "external",
                "external_id": "run-1",
                "goal": "do the thing",
                "status": "complete",
                "outcome": "success",
                "harness": {"agent_id": "coder", "model": "m", "system_prompt": "sp"},
                "step_list": [
                    {
                        "kind": "action",
                        "name": "bash",
                        "args": {"c": "ls"},
                        "result": {"rc": 0},
                        "ok": True,
                    }
                ],
            }
        ]
    }
    r = client.post("/api/trajectories/ingest", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    run_id = r.json()["run_ids"][0]

    r = client.post("/api/trajectories/ingest", json=body)
    assert r.json()["merged"] == 1

    detail = client.get(f"/api/trajectories/runs/{run_id}").json()
    assert detail["goal"] == "do the thing"
    assert detail["harness"] and len(detail["harness"]) == 16
    assert detail["step_list"][0]["args"] == {"c": "ls"}
    assert detail["harness_detail"]["system_prompt"] == "sp"

    listing = client.get("/api/trajectories/runs", params={"dataset": "d1"}).json()
    assert listing["total"] == 1


def test_http_run_404(client):
    assert client.get("/api/trajectories/runs/nope").status_code == 404


def test_http_label_then_filter_by_outcome(client):
    client.post(
        "/api/trajectories/ingest",
        json={"runs": [{"dataset_id": "d1", "goal": "g", "status": "complete"}]},
    )
    run_id = client.get("/api/trajectories/runs").json()["runs"][0]["id"]
    r = client.post(
        f"/api/trajectories/runs/{run_id}/labels",
        json={"key": "outcome", "value": "failure", "source": "human"},
    )
    assert r.status_code == 200
    listing = client.get("/api/trajectories/runs", params={"outcome": "failure"}).json()
    assert listing["total"] == 1


def test_http_harness_listing(client):
    client.post(
        "/api/trajectories/ingest",
        json={
            "runs": [
                {
                    "dataset_id": "d1",
                    "goal": "g",
                    "harness": {
                        "agent_id": "coder",
                        "model": "m",
                        "system_prompt": "a",
                    },
                },
                {
                    "dataset_id": "d1",
                    "goal": "g2",
                    "harness": {
                        "agent_id": "coder",
                        "model": "m",
                        "system_prompt": "b",
                    },
                },
            ]
        },
    )
    harnesses = client.get("/api/trajectories/harnesses").json()["harnesses"]
    # Two prompts, two harnesses — that split is the whole point of the table.
    assert len(harnesses) == 2
    fp = harnesses[0]["fingerprint"]
    assert client.get(f"/api/trajectories/harnesses/{fp}").status_code == 200


def test_tables_land_in_app_db(client, tmp_path):
    """The acceptance property: `traj_*` must be reachable from the database
    console's built-in `app` connection, which is literally this file."""
    import sqlite3

    client.get("/api/trajectories/datasets")
    conn = sqlite3.connect(str(tmp_path / "app.db"))
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {
        "traj_datasets",
        "traj_runs",
        "traj_steps",
        "traj_labels",
        "traj_harnesses",
    } <= names


def test_canonical_json_matches_the_peer_wire_rule(store_mod):
    """Byte-exactness matters: this is the same encoding the mobile wire pins."""
    value = {"b": 1, "a": [3, 2]}
    assert store_mod.canonical_json(value) == json.dumps(
        value, sort_keys=True, separators=(",", ":")
    )


def test_harness_run_count_falls_when_runs_are_deleted(store_mod):
    """`run_count` is derived, not stored.

    It used to be a counter bumped in `start_run`, so deleting a run left the
    harness claiming runs that no longer existed — and the dropdown you pick
    harnesses from in the Harness section is labelled with exactly that number.
    """
    a, _ = store_mod.ingest_run(_write(store_mod))
    b, _ = store_mod.ingest_run(_write(store_mod))
    fingerprint = store_mod.get_run(a).harness
    assert store_mod.get_harness(fingerprint).run_count == 2

    store_mod.delete_run(b)
    assert store_mod.get_harness(fingerprint).run_count == 1
    assert store_mod.list_harnesses()[0].run_count == 1

    store_mod.delete_run(a)
    # The harness definition survives its runs; the count must not.
    assert store_mod.get_harness(fingerprint).run_count == 0


def test_deleting_a_dataset_prunes_its_orphaned_harnesses(store_mod):
    """A harness with no runs is a permanent entry in the compare picker that
    offers an empty report — it is only ever reachable through its runs."""
    store_mod.ingest_run(_write(store_mod, dataset_id="doomed"))
    assert len(store_mod.list_harnesses()) == 1

    store_mod.delete_dataset("doomed")
    assert store_mod.list_harnesses() == []


def test_deleting_one_run_keeps_the_harness(store_mod):
    """Deleting one run of many is not a statement about the configuration, and a
    harness that vanished would take its fingerprint with it — so re-running the
    same configuration would look like a different harness in every earlier report."""
    a, _ = store_mod.ingest_run(_write(store_mod))
    fingerprint = store_mod.get_run(a).harness
    store_mod.delete_run(a)
    assert store_mod.get_harness(fingerprint) is not None
    assert store_mod.get_harness(fingerprint).run_count == 0
