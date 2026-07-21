"""The exported MCP server: turn persistence, redaction, and auth.

The redaction tests are the important ones here. This server hands a node's
trajectories and I/O to an external caller, and the underlying buffers deliberately hold
raw prompts, headers and bodies (see `IoEvent`'s model docstring). A regression that
quietly widens what leaves the process would not fail any other test in the suite.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.modules.interpretability import store
from backend.modules.interpretability.models import (
    ContextBlock,
    RoundSnapshot,
    ToolEntry,
    TurnSnapshot,
)
from backend.modules.mcp import server as export
from backend.modules.mcp.auth import check_token
from backend.modules.telemetry.models import IoEvent

SECRET = "SENSITIVE-PROMPT-CONTENT"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A clean turn store. `conftest.isolate_data_dir` already points
    `HORRIBLE_DATA_DIR` at a per-test tmp dir, so this only has to reset state."""
    store.clear()
    return tmp_path


def _turn(turn_id: str, **kw) -> TurnSnapshot:
    return TurnSnapshot(
        turnId=turn_id,
        agentId=kw.pop("agent_id", "main"),
        model="gemma4:e2b",
        provider="ollama",
        startedAt=kw.pop("started_at", time.time()),
        rounds=[
            RoundSnapshot(
                round=0,
                blocks=[
                    ContextBlock(
                        kind="user",
                        role="user",
                        label="User",
                        content=SECRET,
                        tokens=7,
                        fullChars=len(SECRET),
                    )
                ],
                tools=[ToolEntry(name="mcp-fs.read", group="mcp-fs", tokens=30)],
                messageTokens=7,
                toolTokens=30,
                totalTokens=37,
            )
        ],
        **kw,
    )


# --- persistence ----------------------------------------------------------------


def test_save_and_get_roundtrip(data_dir: Path) -> None:
    store.save_turn(_turn("t1"))
    got = store.get_turn("t1")
    assert got is not None
    assert got.turnId == "t1"
    assert got.rounds[0].blocks[0].content == SECRET
    assert store.get_turn("nope") is None


def test_save_is_idempotent_upsert(data_dir: Path) -> None:
    """A turn is persisted once per round, so re-saving must update, not duplicate."""
    turn = _turn("t1")
    store.save_turn(turn)
    turn.rounds.append(RoundSnapshot(round=1, totalTokens=99))
    store.save_turn(turn)
    listed = store.list_turns()
    assert len(listed) == 1
    assert listed[0]["rounds"] == 2


def test_total_tokens_is_last_round_not_sum(data_dir: Path) -> None:
    """Rounds are cumulative — summing them would multiply-count the same context."""
    turn = _turn("t1")
    turn.rounds.append(RoundSnapshot(round=1, totalTokens=50))
    store.save_turn(turn)
    assert store.list_turns()[0]["totalTokens"] == 50


def test_list_turns_filters_and_orders(data_dir: Path) -> None:
    store.save_turn(_turn("old", started_at=1000.0))
    store.save_turn(_turn("new", started_at=2000.0))
    store.save_turn(_turn("coder-turn", started_at=1500.0, agent_id="coder"))

    ids = [t["turnId"] for t in store.list_turns()]
    assert ids == ["new", "coder-turn", "old"]  # most recent first

    assert [t["turnId"] for t in store.list_turns(agent_id="coder")] == ["coder-turn"]
    assert [t["turnId"] for t in store.list_turns(since=1400.0)] == [
        "new",
        "coder-turn",
    ]
    assert len(store.list_turns(limit=1)) == 1


def test_listings_carry_no_context_blocks(data_dir: Path) -> None:
    """A listing is metadata; prompt text must not ride along in it."""
    store.save_turn(_turn("t1"))
    assert SECRET not in str(store.list_turns())


def test_roots_only_hides_delegated_subturns(data_dir: Path) -> None:
    store.save_turn(_turn("parent"))
    store.save_turn(_turn("child", parentTurnId="parent", agent_id="coder"))
    assert [t["turnId"] for t in store.list_turns(roots_only=True)] == ["parent"]
    assert len(store.list_turns(roots_only=False)) == 2


def test_tree_nests_delegated_turns(data_dir: Path) -> None:
    store.save_turn(_turn("parent"))
    store.save_turn(_turn("child", parentTurnId="parent", agent_id="coder"))
    tree = store.get_tree("parent")
    assert tree is not None
    assert tree["turnId"] == "parent"
    assert [c["turnId"] for c in tree["children"]] == ["child"]
    assert tree["children"][0]["children"] == []


def test_tree_survives_a_parent_cycle(data_dir: Path) -> None:
    """A corrupted parent pointer must not recurse forever."""
    store.save_turn(_turn("a", parentTurnId="b"))
    store.save_turn(_turn("b", parentTurnId="a"))
    tree = store.get_tree("a")
    assert tree is not None  # bounded by the depth cap rather than hanging


def test_prune_keeps_the_newest(data_dir: Path) -> None:
    for i in range(10):
        store.save_turn(_turn(f"t{i}", started_at=1000.0 + i))
    assert store.prune(keep=4) == 6
    ids = [t["turnId"] for t in store.list_turns()]
    assert ids == ["t9", "t8", "t7", "t6"]


def test_stats_aggregates_by_agent(data_dir: Path) -> None:
    store.save_turn(_turn("a", started_at=1000.0))
    store.save_turn(_turn("b", started_at=2000.0, agent_id="coder"))
    s = store.stats()
    assert s["turns"] == 2
    assert s["byAgent"] == {"main": 1, "coder": 1}
    assert s["earliest"] == 1000.0 and s["latest"] == 2000.0


def test_store_failures_are_swallowed(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observation must never break the turn it observes."""
    monkeypatch.setattr(
        store, "ensure_app_db_dir", lambda: (_ for _ in ()).throw(OSError("disk gone"))
    )
    store.save_turn(_turn("t1"))  # must not raise
    assert store.list_turns() == []
    assert store.get_turn("t1") is None


# --- redaction ------------------------------------------------------------------


def test_turn_detail_redacts_content_by_default() -> None:
    detail = export._turn_detail(_turn("t1"), with_content=False)
    assert detail["contentRedacted"] is True
    block = detail["rounds"][0]["blocks"][0]
    # Shape and cost survive; the text does not.
    assert block["tokens"] == 7
    assert block["chars"] == len(SECRET)
    assert "content" not in block
    assert SECRET not in str(detail)


def test_turn_detail_includes_content_when_enabled() -> None:
    """The flag must actually change behaviour — a redactor that always redacts
    would pass the test above while making the setting a lie."""
    detail = export._turn_detail(_turn("t1"), with_content=True)
    assert detail["contentRedacted"] is False
    assert detail["rounds"][0]["blocks"][0]["content"] == SECRET


def test_event_summary_drops_bodies_and_headers() -> None:
    event = IoEvent(
        id=1,
        ts=time.time(),
        source="outbound",
        method="POST",
        target="https://api.example/v1",
        status=200,
        request_headers={"Authorization": "Bearer SUPER-SECRET"},
        response_headers={"Set-Cookie": "session=abc"},
        request_body='{"api_key": "SUPER-SECRET"}',
        response_body='{"token": "SUPER-SECRET"}',
    )
    summary = export._event_summary(event)
    assert summary["method"] == "POST"
    assert summary["status"] == 200
    for field in (
        "request_headers",
        "response_headers",
        "request_body",
        "response_body",
    ):
        assert field not in summary
    assert "SUPER-SECRET" not in str(summary)


def test_event_summary_is_an_allow_list() -> None:
    """A new IoEvent field must not leak just because nobody excluded it."""

    class Sneaky(IoEvent):
        newly_added_secret: str = "LEAK"

    summary = export._event_summary(
        Sneaky(id=1, ts=0.0, source="inbound", method="GET", target="/")
    )
    assert "LEAK" not in str(summary)


# --- gating and auth ------------------------------------------------------------


def test_export_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(export.ENABLE_ENV, raising=False)
    assert export.is_enabled() is False


def test_export_enables_only_on_exact_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(export.ENABLE_ENV, "true")
    assert export.is_enabled() is False  # only "1" counts
    monkeypatch.setenv(export.ENABLE_ENV, "1")
    assert export.is_enabled() is True


def test_mount_is_a_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(export.ENABLE_ENV, raising=False)

    class FakeApp:
        def mount(self, *a, **k):  # pragma: no cover - must never be reached
            raise AssertionError("mounted while disabled")

    assert export.mount(FakeApp()) is False


def test_check_token_rejects_mismatch_and_empty() -> None:
    assert check_token("abc", "abc") is True
    assert check_token("abc", "abd") is False
    assert check_token("", "abc") is False
    assert check_token(None, "abc") is False
    assert check_token("abc", "") is False


def test_every_exported_tool_is_read_only() -> None:
    """This is an interpretability surface. A write tool here would be a
    remote-control endpoint on a token."""
    server = export.build_server()
    import asyncio

    tools = asyncio.run(server.list_tools())
    assert tools, "expected exported tools"
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.readOnlyHint is True, tool.name
