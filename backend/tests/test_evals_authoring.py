"""The authoring tools: creating suites and cases from the agent.

These exist because drafting twenty cases from a set of tool descriptions is the
tedious work worth delegating. What makes that safe is not withholding the tool, it
is that a suite is a `.jsonl` you review as a diff — plus the two guards below,
which cover the failure mode an authoring agent actually has: enthusiasm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.evals import bundled, store
from backend.modules.evals.agent_tools import (
    _add_case,
    _create_suite,
    _fork,
    _list_suites,
    _remove_case,
)


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    return TestClient(app)


def a_case(case_id: str = "c1") -> dict:
    return {
        "id": case_id,
        "prompt": "open a terminal",
        "expect": {"grade": "name_only", "calls": [{"name": "show"}]},
    }


@pytest.mark.anyio
async def test_the_agent_can_create_a_suite_and_add_a_case(node):
    created = await _create_suite(name="Karaoke tools")
    assert created["cases"] == 0
    assert created["path"].endswith(".jsonl")

    added = await _add_case(suite_id=created["id"], case=a_case())
    assert added == {"added": "c1", "cases": 1}

    # It really landed in the file, in the format everything else reads.
    lines = [
        line
        for line in Path(created["path"]).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert json.loads(lines[0])["id"] == "c1"


@pytest.mark.anyio
async def test_a_duplicate_case_id_is_refused_not_overwritten(node):
    """The one unrecoverable thing an authoring tool can do is silently replace a
    case somebody wrote — results are keyed by (run, case)."""
    created = await _create_suite(name="S")
    await _add_case(suite_id=created["id"], case=a_case())

    again = await _add_case(suite_id=created["id"], case=a_case())
    assert "already exists" in again["error"]
    assert "removeCase" in again["error"], "the refusal must say how to proceed"

    suite = store.get_suite(created["id"])
    assert suite and suite.case_count == 1


@pytest.mark.anyio
async def test_a_case_that_does_not_validate_is_reported_with_the_reason(node):
    """The model needs the validation text to fix it on the next round; 'invalid'
    on its own costs a round and teaches nothing."""
    created = await _create_suite(name="S")
    bad = await _add_case(suite_id=created["id"], case={"prompt": "no id here"})
    assert "does not validate" in bad["error"]
    assert "id" in bad["error"]


@pytest.mark.anyio
async def test_a_case_handed_over_as_a_json_string_is_accepted(node):
    """Small models routinely stringify an object-typed argument. Accepting both is
    cheaper than a retry round, and the schema is validated either way."""
    created = await _create_suite(name="S")
    added = await _add_case(suite_id=created["id"], case=json.dumps(a_case("c2")))
    assert added == {"added": "c2", "cases": 1}


@pytest.mark.anyio
async def test_a_string_that_is_not_json_says_so(node):
    created = await _create_suite(name="S")
    bad = await _add_case(suite_id=created["id"], case="not json at all")
    assert "not valid JSON" in bad["error"]


@pytest.mark.anyio
async def test_the_agent_cannot_write_to_a_bundled_suite(node):
    """It would be editing a file in the repo. The refusal names the way through."""
    starter = f"{bundled.PREFIX}starter"
    refused = await _add_case(suite_id=starter, case=a_case())
    assert "cannot be edited" in refused["error"]
    assert "fork" in refused["error"]


@pytest.mark.anyio
async def test_the_agent_can_fork_a_bundled_suite_and_then_edit_it(node):
    forked = await _fork(suite_id=f"{bundled.PREFIX}starter", name="Mine")
    assert forked["cases"] > 0

    added = await _add_case(suite_id=forked["id"], case=a_case("extra"))
    assert added["added"] == "extra"
    assert added["cases"] == forked["cases"] + 1


@pytest.mark.anyio
async def test_listing_says_which_suites_are_read_only(node):
    """So the model does not spend a round discovering it by failing a write."""
    listed = await _list_suites()
    starter = next(s for s in listed["suites"] if s["source"] == "bundled")
    assert starter["readOnly"] is True

    await _create_suite(name="Mine")
    listed = await _list_suites()
    mine = next(s for s in listed["suites"] if s["name"] == "Mine")
    assert mine["readOnly"] is False


@pytest.mark.anyio
async def test_removing_a_case(node):
    created = await _create_suite(name="S")
    await _add_case(suite_id=created["id"], case=a_case("a"))
    await _add_case(suite_id=created["id"], case=a_case("b"))

    assert (await _remove_case(suite_id=created["id"], case_id="a")) == {
        "removed": "a",
        "cases": 1,
    }
    missing = await _remove_case(suite_id=created["id"], case_id="a")
    assert "no case" in missing["error"]


@pytest.mark.anyio
async def test_every_authoring_tool_is_gated_as_side_effecting(node):
    """They write files. A read-only tool skips the permission gate entirely, so
    getting this wrong means an agent editing suites with no prompt."""
    from backend.modules.evals.agent_tools import _TOOLS

    writers = {"evals.createSuite", "evals.fork", "evals.addCase", "evals.removeCase"}
    for tool in _TOOLS:
        if tool.name in writers:
            assert tool.side_effect, f"{tool.name} writes but is not gated"
