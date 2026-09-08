"""One place to see what this node made.

`lineage.py` recorded the join — recipe, checkpoint, GGUF, base model, training run —
and its own docstring proposed the query, and nothing performed it. Five disjoint
views of "the models I have" existed (the llama.cpp catalog, HF downloads,
chat-provider model *names*, per-project checkpoints, and this table), so "which
fine-tune is this, and did it beat its base?" was answered by writing a join by hand
in the database console.

Asserted against the **HTTP body**, not `inventory()`'s return value: a route with a
Pydantic `response_model` silently drops any field it does not declare, which is how a
served field reaches the browser as `undefined` with nothing failing. This route
declares none, and this is what holds it to that.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.training import lineage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    lineage._initialized.clear()

    from backend.modules.training.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def a_converted_model(tmp_path):
    """One lineage row whose GGUF actually exists on disk."""
    gguf = tmp_path / "my-finetune-f16.gguf"
    gguf.write_bytes(b"not really a gguf")
    lineage.record(
        str(gguf),
        project_id="proj-1",
        checkpoint="outputs/checkpoint-1200",
        base_model="meta-llama/Llama-3.2-3B",
        out_type="f16",
        recipe={"learning_rate": 0.0002},
        localtrack_run_id="lt-1",
    )
    return gguf


def test_empty_node_reports_no_models(client) -> None:
    body = client.get("/api/training/inventory").json()
    # Not a 404 and not an error: a node that has fine-tuned nothing is a normal
    # state, and the pane renders it as an empty section rather than a failure.
    assert body == {"models": []}


def test_a_converted_model_carries_its_provenance(client, a_converted_model) -> None:
    body = client.get("/api/training/inventory").json()
    assert len(body["models"]) == 1
    row = body["models"][0]
    # The fields the row exists to show. Read off the HTTP body: a response model
    # would have dropped every one of these without failing.
    assert row["ggufPath"] == str(a_converted_model)
    assert row["baseModel"] == "meta-llama/Llama-3.2-3B"
    assert row["projectId"] == "proj-1"
    assert row["checkpoint"] == "outputs/checkpoint-1200"
    assert row["present"] is True
    assert row["sizeBytes"] > 0


def test_a_deleted_file_is_reported_rather_than_hidden(
    client, a_converted_model
) -> None:
    a_converted_model.unlink()
    row = client.get("/api/training/inventory").json()["models"][0]
    # The one fact worth reporting loudly: a fine-tune whose history you can read and
    # whose file you cannot serve. Dropping the row would lose the provenance too.
    assert row["present"] is False
    assert row["sizeBytes"] == 0
    assert row["baseModel"] == "meta-llama/Llama-3.2-3B"


def test_missing_legs_degrade_to_empty(client, a_converted_model) -> None:
    # No eval has ever run and no metrics were mirrored. Every leg of this join is
    # optional, and an inventory that 500s because one is absent would be useless
    # exactly when it is most needed.
    row = client.get("/api/training/inventory").json()["models"][0]
    assert row["evals"] == []
    assert row["metrics"] == {}


def test_scores_attach_by_path_not_by_name(
    client, a_converted_model, monkeypatch
) -> None:
    """A score is attributed to the FILE it measured.

    `model` is an alias a server answers to and may mean a different file tomorrow,
    which is why the lineage table is keyed on the path. A run from before
    `model_path` existed carries an empty one and is deliberately not attributed —
    better than attaching a score to a file it may never have measured.
    """

    class _Run:
        def __init__(self, run_id, path, passed, status="done"):
            self.id = run_id
            self.suite_id = "tool-calling"
            self.model_path = path
            self.passed = passed
            self.total = 12
            self.completed = 12
            self.finished_at = "2026-09-07T00:00:00Z"
            self.status = status

    from backend.modules.evals import store as evals_store

    monkeypatch.setattr(
        evals_store,
        "list_runs",
        lambda **_: [
            _Run("r1", str(a_converted_model), 9),
            _Run("r2", "/some/other/model.gguf", 4),
            _Run("r3", "", 11),  # pre-column row: no path, no attribution
            _Run("r4", str(a_converted_model), 0, status="failed"),  # never finished
        ],
    )

    row = client.get("/api/training/inventory").json()["models"][0]
    assert [e["runId"] for e in row["evals"]] == ["r1"]
    assert row["evals"][0]["passed"] == 9
