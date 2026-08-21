"""Importers, tested as pure functions against fixtures.

These formats belong to other people and change without telling you, and the
failure mode is a silently mangled trajectory rather than an exception — which is
exactly why they get fixtures rather than a live round-trip.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.trajectories.adapters import games, importers


# --- claude-code ------------------------------------------------------------

CLAUDE_CODE = "\n".join(
    json.dumps(record)
    for record in [
        {
            "type": "system",
            "message": {"role": "system", "content": "You are Claude Code."},
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "the auth test fails"},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [
                    {"type": "text", "text": "Let me run it."},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Bash",
                        "input": {"command": "pytest -k auth"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "1 failed",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_2",
                        "name": "Read",
                        "input": {"path": "auth.py"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_2",
                        "content": {"is_error": True, "text": "no such file"},
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Fixed."}],
            },
        },
    ]
)


def test_claude_code_pairs_tool_results_across_records():
    """The whole job: a `tool_use` and its `tool_result` are different records,
    and an action has to carry its own observation."""
    (write,) = importers.from_claude_code(CLAUDE_CODE, "d")
    actions = [s for s in write.step_list if s.kind == "action"]
    assert [a.name for a in actions] == ["Bash", "Read"]
    assert actions[0].args == {"command": "pytest -k auth"}
    assert actions[0].result == "1 failed"
    assert actions[0].ok is True
    # `is_error` on the result is the only signal a call failed.
    assert actions[1].ok is False


def test_claude_code_takes_goal_model_and_prompt():
    (write,) = importers.from_claude_code(CLAUDE_CODE, "d")
    assert write.goal == "the auth test fails"
    assert write.model == "claude-opus-5"
    assert write.harness.system_prompt == "You are Claude Code."
    assert write.source == "imported"


def test_claude_code_leaves_the_run_ungraded():
    """Nothing in a transcript says the session went well. Inventing `success`
    would push unvetted runs straight into the SFT export."""
    (write,) = importers.from_claude_code(CLAUDE_CODE, "d")
    assert write.outcome is None


def test_claude_code_does_not_file_tool_result_carriers_as_user_turns():
    """A user record that only carries tool results is transport, not a turn."""
    (write,) = importers.from_claude_code(CLAUDE_CODE, "d")
    users = [s for s in write.step_list if s.kind == "message" and s.role == "user"]
    assert [u.content for u in users] == ["the auth test fails"]


def test_claude_code_keeps_the_raw_records():
    (write,) = importers.from_claude_code(CLAUDE_CODE, "d")
    assert write.meta["format"] == "claude-code"
    assert len(write.meta["raw"]) == 7


# --- openai -----------------------------------------------------------------

OPENAI = json.dumps(
    {
        "model": "gpt-x",
        "tools": [{"type": "function", "function": {"name": "bash"}}],
        "messages": [
            {"role": "system", "content": "You are a coder."},
            {"role": "user", "content": "list the files"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "a.py b.py"},
            {"role": "assistant", "content": "Two files."},
        ],
    }
)


def test_openai_parses_the_arguments_string():
    """OpenAI sends tool arguments as a JSON *string*, not an object."""
    (write,) = importers.from_openai(OPENAI, "d")
    action = [s for s in write.step_list if s.kind == "action"][0]
    assert action.args == {"cmd": "ls"}
    assert action.result == "a.py b.py"
    assert write.harness.tool_names == ["bash"]
    assert write.model == "gpt-x"


def test_openai_keeps_malformed_arguments_as_written():
    """A model that emitted broken JSON is a fact about the run worth keeping —
    dropping it would erase the evidence of the failure being studied."""
    payload = json.dumps(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "bash", "arguments": '{"cmd": '}}
                ],
            },
        ]
    )
    (write,) = importers.from_openai(payload, "d")
    action = [s for s in write.step_list if s.kind == "action"][0]
    assert action.args == '{"cmd": '


def test_openai_accepts_a_bare_message_list():
    (write,) = importers.from_openai(
        json.dumps([{"role": "user", "content": "hi"}]), "d"
    )
    assert write.goal == "hi"


# --- plain messages ---------------------------------------------------------


def test_messages_import():
    (write,) = importers.from_messages(
        json.dumps(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        ),
        "d",
    )
    assert [s.role for s in write.step_list] == ["user", "assistant"]
    assert write.goal == "hello"


# --- failure modes ----------------------------------------------------------


def test_bad_jsonl_reports_the_line_number():
    """ "Expecting value" with no position is not a diagnosis in a 30k-line file."""
    with pytest.raises(importers.ImportFormatError, match="line 2"):
        importers.parse_jsonl('{"a": 1}\nnot json\n')


def test_wrong_format_is_refused_loudly():
    """An importer that quietly produces zero runs is indistinguishable from an
    empty file, and the user concludes the feature is broken."""
    with pytest.raises(importers.ImportFormatError):
        importers.from_claude_code(json.dumps({"nope": 1}), "d")
    with pytest.raises(importers.ImportFormatError):
        importers.from_openai("{not json", "d")
    with pytest.raises(importers.ImportFormatError, match="unknown format"):
        importers.import_any("parquet", "", "d")


# --- games ------------------------------------------------------------------

REPLAY = {
    "id": "r1",
    "game_id": "tictactoe",
    "seats": ["alice", "bob"],
    "winner": 0,
    "returns": {"0": 1.0, "1": -1.0},
    "events": [
        {"kind": "public_state", "state": {"board": "..."}},
        {"kind": "trace", "seat": 0, "steps": ["centre is strong"]},
        {"kind": "action", "seat": 0, "action_id": "a4"},
        {"kind": "public_state", "state": {"board": "..x.."}},
        {"kind": "action", "seat": 1, "action_id": "a0", "timeout": True},
        {"kind": "game_over", "returns": {"0": 1.0, "1": -1.0}, "winner": 0},
    ],
}


def test_replay_becomes_one_run_per_seat():
    """Two agents making their own decisions are two trajectories. Folding them
    into one would interleave two policies and void every per-harness aggregate."""
    writes = games.replay_to_writes(REPLAY, "games")
    assert len(writes) == 2
    assert [w.external_id for w in writes] == ["replay:r1:0", "replay:r1:1"]
    assert [w.outcome for w in writes] == ["success", "failure"]
    assert [w.reward for w in writes] == [1.0, -1.0]


def test_replay_action_carries_the_state_it_was_taken_from():
    writes = games.replay_to_writes(REPLAY, "games")
    seat0 = writes[0]
    kinds = [s.kind for s in seat0.step_list]
    assert kinds == ["thought", "observation", "action", "reward"]
    assert seat0.step_list[1].result == {"board": "..."}


def test_a_timed_out_move_is_recorded_as_a_failure():
    """A timeout is a real failure mode of an agent under a time budget, and the
    single most useful thing a replay can tell you."""
    seat1 = games.replay_to_writes(REPLAY, "games")[1]
    action = [s for s in seat1.step_list if s.kind == "action"][0]
    assert action.ok is False
    assert action.error == "timeout"


def test_replay_with_no_verdict_stays_ungraded():
    """Guessing an outcome produces wrongly-labelled training data."""
    bare = {
        "id": "r2",
        "game_id": "g",
        "events": [{"kind": "action", "seat": 0, "action_id": "x"}],
    }
    (write,) = games.replay_to_writes(bare, "games")
    assert write.outcome is None
    assert write.reward is None


def test_replay_ids_are_stable_so_reimport_updates(store_ready):
    """Re-importing a replay must not file a second copy of it."""
    from backend.modules.trajectories import store

    for _ in range(2):
        for write in games.replay_to_writes(REPLAY, "games"):
            store.ingest_run(write)
    _, total = store.list_runs(dataset_id="games")
    assert total == 2


@pytest.fixture()
def store_ready():
    from backend.modules.trajectories import store

    store._initialized.clear()
    store.init_trajectories_db()
    return store
