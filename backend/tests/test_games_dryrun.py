"""Tests for the harness dry-run tester: engine position sampling, the full-loop
`run_dry` orchestration (trace, timing, compile errors, model preflight), and the
HTTP surface (`/games/dry-run`, `/games/loadout/validate`)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.modules.games.dryrun import run_dry, sample_observation
from backend.modules.games.loadout import Loadout, ToolDef, tool_name_error


class _Call:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments
        self.id = name


class _Result:
    def __init__(self, content: str = "", tool_calls: list[_Call] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.assistant_message = {"role": "assistant", "content": content}


def _scripted(turns: list[_Result]):
    it = iter(turns)

    async def chat(messages, tools):
        return next(it)

    return chat


def _loadout(**kwargs) -> Loadout:
    return Loadout(game_id="tictactoe", **kwargs)


# ---- sample_observation ------------------------------------------------------


def test_sample_observation_tictactoe() -> None:
    obs, legal = sample_observation("tictactoe")
    assert len(obs["board"]) == 9
    assert len(legal) == 9
    assert all("id" in a and "label" in a for a in legal)


def test_sample_observation_holdem_resolves_the_deal() -> None:
    obs, legal = sample_observation("holdem", seed=7)
    assert len(obs["hole"]) == 2  # chance node resolved: we have hole cards
    assert obs["seat"] == 0  # the button acts first preflop
    assert legal  # fold/call/raises

    # The same seed deals the same hand; a different seed (very likely) doesn't.
    again, _ = sample_observation("holdem", seed=7)
    assert again["hole"] == obs["hole"]


def test_sample_observation_rag_race_simultaneous_seats() -> None:
    obs, legal = sample_observation("rag_race")
    assert obs["seat"] == 0  # first acting seat of a simultaneous game
    assert obs["docs"]
    assert legal


def test_sample_observation_unknown_game_raises() -> None:
    with pytest.raises(KeyError):
        sample_observation("not_a_game")


# ---- run_dry ------------------------------------------------------------------


def test_run_dry_traces_the_full_loop() -> None:
    loadout = _loadout(
        context="scan first",
        tools=[
            ToolDef(
                name="scan",
                description="",
                code="def run(args, obs):\n    return {'seen': True}\n",
            )
        ],
    )
    chat = _scripted(
        [
            _Result("looking", [_Call("scan", {})]),
            _Result("center", [_Call("game.chooseAction", {"action_id": "4"})]),
        ]
    )
    res = asyncio.run(run_dry(loadout, "tictactoe", chat_fn=chat))
    assert res.ok and res.error is None
    assert res.chosen == "4"
    assert [s.kind for s in res.steps] == [
        "assistant",
        "tool_result",
        "assistant",
        "chose",
    ]
    assert all(s.t_ms >= 0 for s in res.steps)
    assert res.rounds_used == 2
    assert res.total_ms >= 0
    assert res.compile_errors == {}
    assert len(res.legal_actions) == 9


def test_run_dry_reports_compile_errors_but_still_runs() -> None:
    loadout = _loadout(
        tools=[ToolDef(name="broken", description="", code="def run(:\n")]
    )
    chat = _scripted([_Result("", [_Call("game.chooseAction", {"action_id": "0"})])])
    res = asyncio.run(run_dry(loadout, "tictactoe", chat_fn=chat))
    assert res.ok
    assert res.chosen == "0"
    assert "broken" in res.compile_errors


def test_run_dry_without_any_model_is_a_clean_error(monkeypatch) -> None:
    import backend.modules.agent.routes as agent_routes

    monkeypatch.setattr(agent_routes, "_load_config", lambda: None)
    res = asyncio.run(run_dry(_loadout(), "tictactoe"))
    assert not res.ok
    assert "no model" in (res.error or "")
    assert res.steps == []
    assert res.legal_actions  # the sample position still comes back


def test_run_dry_surfaces_chat_exceptions_with_partial_steps() -> None:
    async def chat(messages, tools):
        raise RuntimeError("provider down")

    res = asyncio.run(run_dry(_loadout(), "tictactoe", chat_fn=chat))
    assert not res.ok
    assert "RuntimeError" in (res.error or "")


def test_run_dry_never_committing_uses_the_whole_budget() -> None:
    async def chat(messages, tools):
        return _Result("hmm", [_Call("scan", {})])  # unknown tool, never commits

    loadout = _loadout()
    res = asyncio.run(run_dry(loadout, "tictactoe", chat_fn=chat))
    assert res.ok  # not an error: the agent just never committed
    assert res.chosen is None
    assert res.rounds_used == 6  # MAX_HARNESS_ROUNDS


# ---- run_once (policy seam) ----------------------------------------------------


def test_run_once_propagates_exceptions_and_commits() -> None:
    from backend.modules.games.policy import AgentPolicy

    legal = [{"id": "0", "label": "a"}, {"id": "4", "label": "b"}]

    async def boom(messages, tools):
        raise RuntimeError("down")

    policy = AgentPolicy(chat_fn=boom, load_loadout=lambda _g: _loadout())
    with pytest.raises(RuntimeError):
        asyncio.run(policy.run_once({}, legal))

    ok = AgentPolicy(
        chat_fn=_scripted(
            [_Result("", [_Call("game.chooseAction", {"action_id": "4"})])]
        ),
        load_loadout=lambda _g: _loadout(),
    )
    assert asyncio.run(ok.run_once({}, legal)) == "4"


# ---- tool_name_error -----------------------------------------------------------


def test_tool_name_rule() -> None:
    assert tool_name_error("board_scanner") is None
    assert tool_name_error("fighter.bot") is None  # dots stay legal
    assert tool_name_error("") is not None
    assert tool_name_error("9lives") is not None
    assert tool_name_error("has space") is not None
    assert tool_name_error("game.chooseAction") is not None  # reserved prefix
    assert tool_name_error("scan", {"scan"}) is not None  # duplicate


# ---- starter templates ----------------------------------------------------------


def test_every_template_tool_compiles_and_is_well_named() -> None:
    """Starter templates must be shippable as-is: valid names, compiling code."""
    from backend.modules.games.loadout import HarnessRuntime
    from backend.modules.games.templates import loadout_templates

    templates = loadout_templates()
    ids = {t["id"] for t in templates}
    assert {"ttt-tactician", "holdem-calculator"} <= ids  # the multi-tool starters
    for descriptor in templates:
        loadout = Loadout.from_wire(descriptor["game_id"], dict(descriptor["loadout"]))
        assert HarnessRuntime(loadout).compile_errors() == {}, descriptor["id"]
        taken: set[str] = set()
        for tool in loadout.tools:
            assert tool_name_error(tool.name, taken) is None, tool.name
            taken.add(tool.name)


def test_multi_tool_templates_run_against_a_sample_position() -> None:
    """The two-tool starters actually execute against real engine observations."""
    from backend.modules.games.loadout import HarnessRuntime
    from backend.modules.games.templates import loadout_templates

    by_id = {t["id"]: t for t in loadout_templates()}

    ttt = Loadout.from_wire("tictactoe", dict(by_id["ttt-tactician"]["loadout"]))
    obs, _ = sample_observation("tictactoe")
    runtime = HarnessRuntime(ttt)
    scan = asyncio.run(runtime.call("board_scanner", {}, obs))
    forks = asyncio.run(runtime.call("fork_finder", {}, obs))
    assert "win_at" in scan and "my_forks" in forks

    holdem = Loadout.from_wire("holdem", dict(by_id["holdem-calculator"]["loadout"]))
    obs, _ = sample_observation("holdem", seed=3)
    runtime = HarnessRuntime(holdem)
    strength = asyncio.run(runtime.call("hand_strength", {}, obs))
    odds = asyncio.run(runtime.call("pot_odds", {"to_call": obs["to_call"]}, obs))
    assert strength["street"] == "preflop"
    assert "break_even_equity" in odds


# ---- HTTP surface ---------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    from backend.app import app

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def _tool(name: str, code: str = "def run(args, obs):\n    return 1\n") -> dict:
    return {"name": name, "description": "", "code": code}


def test_validate_route_diagnoses_each_tool(client: TestClient) -> None:
    body = {
        "game_id": "tictactoe",
        "context": "",
        "tools": [
            _tool("fine"),
            _tool("fine"),  # duplicate
            _tool("game.cheat"),  # reserved prefix
            _tool("9lives"),  # bad identifier
            _tool("broken", code="def run(:\n"),  # compile error
        ],
    }
    res = client.post("/api/games/loadout/validate", json=body)
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    by_name = {}
    for d in payload["tools"]:
        by_name.setdefault(d["name"], []).append(d)
    assert by_name["fine"][0]["ok"] is True
    assert "duplicate" in by_name["fine"][1]["error"]
    assert "reserved" in by_name["game.cheat"][0]["error"]
    assert by_name["9lives"][0]["ok"] is False
    assert by_name["broken"][0]["ok"] is False


def test_validate_route_clean_loadout_is_ok(client: TestClient) -> None:
    body = {"game_id": "tictactoe", "context": "", "tools": [_tool("scan")]}
    res = client.post("/api/games/loadout/validate", json=body)
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_dry_run_route_rejects_unknown_game(client: TestClient) -> None:
    body = {"game_id": "bogus", "loadout": {"game_id": "bogus", "tools": []}}
    res = client.post("/api/games/dry-run", json=body)
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert "unknown game" in payload["error"]


def test_dry_run_route_without_a_model_reports_it(client: TestClient) -> None:
    # Isolated data dir -> no loadout model, no agent config -> clean preflight error.
    body = {"game_id": "tictactoe", "loadout": {"game_id": "tictactoe", "tools": []}}
    res = client.post("/api/games/dry-run", json=body)
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert "no model" in payload["error"]
    assert len(payload["legal_actions"]) == 9
