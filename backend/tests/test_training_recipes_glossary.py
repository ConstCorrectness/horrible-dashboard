"""The Learn strip's glossary is the recipe form's own help text, served once."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.training import recipes


def test_glossary_lists_each_knob_once_with_help():
    terms = recipes.glossary()
    names = [t["name"] for t in terms]
    assert len(names) == len(set(names))
    assert all(t["help"] for t in terms)
    # The knobs a first fine-tune is made of must be explainable.
    assert {"learning_rate", "num_train_epochs"} <= set(names)


def test_glossary_route_serves_it():
    res = TestClient(app).get("/api/training/learn/glossary")
    assert res.status_code == 200
    assert res.json()["terms"] == recipes.glossary()
