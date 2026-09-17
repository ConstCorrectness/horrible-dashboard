"""Running a suite on a friend's agent: gates, human accept, attestation, bounds.

Both roles live in one process here — `_outgoing` (offerer) and `_incoming` (runner)
are separate registries — joined by a loopback hub that dispatches each message to
the handler the real fabric would, tagged with the node it came from.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.modules.evals import fabric, leaderboard, store
from backend.modules.evals.models import CaseResult, EvalCase, Expect, ToolCall
from backend.modules.network.models import PeerEnvelope

OFFERER = "offerernode00000"
RUNNER = "runnernode000000"
STRANGER = "strangernode0000"
SECRET = "sk-live-0123456789abcdefghijklmnop"


class Session:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.info = SimpleNamespace(node_id=node_id, trusted=trusted)


class TwoNodeHub:
    """Every message is delivered to the other node's handler, never a real socket."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.trusted = True
        self._replies: dict[str, PeerEnvelope] = {}
        self._n = 0
        self.peers = {RUNNER: SimpleNamespace(info=SimpleNamespace(trusted=True))}

    def register_handler(self, msg_type: str, handler: Any, mode: str = "") -> None:
        self.handlers[msg_type] = handler

    @staticmethod
    def _other(node_id: str) -> str:
        return OFFERER if node_id == RUNNER else RUNNER

    def _env(self, msg_type: str, data: dict[str, Any], src: str, re=None):
        self._n += 1
        return PeerEnvelope(
            type=msg_type, msg_id=f"m{self._n}", src=src, re=re, ts=0.0, data=data
        )

    async def request(self, node_id, msg_type, data, timeout: float = 0):
        env = self._env(msg_type, data, self._other(node_id))
        await self.handlers[msg_type](self, Session(env.src, self.trusted), env)
        if env.msg_id not in self._replies:
            raise TimeoutError("no reply")
        return self._replies.pop(env.msg_id)

    async def send_to(self, node_id, msg_type, data, re=None):
        # Every message must fit the dialer's 1 MiB frame, with room to spare.
        assert len(json.dumps(data, default=str)) < 512 * 1024
        self.sent.append((node_id, msg_type, data))
        if re is not None:
            self._replies[re] = self._env(msg_type, data, self._other(node_id), re)
            return
        env = self._env(msg_type, data, self._other(node_id))
        handler = self.handlers.get(msg_type)
        if handler is not None:
            await handler(self, Session(env.src, self.trusted), env)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    fabric._incoming.clear()
    fabric._outgoing.clear()
    fabric._active_remote_suites.clear()

    hub = TwoNodeHub()
    import backend.modules.network.capabilities as caps
    import backend.modules.network.hub as hub_mod

    monkeypatch.setattr(hub_mod, "peer_hub", hub)
    monkeypatch.setattr(caps, "register_static", lambda *a, **k: None)
    fabric.register(hub)

    state = {"accepting": True, "ran": []}
    monkeypatch.setattr(fabric, "accepting", lambda: state["accepting"])
    monkeypatch.setattr(
        fabric,
        "_local_target",
        lambda: (SimpleNamespace(kind="ollama"), "http://runner", "qwen3:8b"),
    )
    monkeypatch.setattr(fabric, "_friend_name", lambda node: f"friend-{node[:4]}")
    monkeypatch.setattr(
        fabric.fingerprint, "compute", lambda: ("runnerhash01", '{"skills":[]}')
    )

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(fabric, "_emit", quiet)
    monkeypatch.setattr(fabric, "_notify_offer", quiet)

    async def scripted(case, tools, **kwargs):
        state["ran"].append((case.id, kwargs.get("model")))
        return CaseResult(
            case_id=case.id,
            passed=case.id != "c2",
            grade=case.expect.grade,
            detail="graded on the runner",
            actual=[ToolCall(name="show", arguments={"api_key": SECRET})],
            answer=f"my key is {SECRET}",
            turn_id="runner-private-turn",
            case_hash="runner-claims-this",
        )

    import backend.modules.evals.runner_agent as runner_agent

    monkeypatch.setattr(runner_agent, "run_case", scripted)

    suite = store.create_suite("Remote")
    store.write_cases(
        suite,
        [
            EvalCase(
                id="c1",
                prompt="open it",
                expect=Expect(grade="subset", calls=[ToolCall(name="show")]),
            ),
            EvalCase(id="c2", prompt="say no", expect=Expect(grade="no_call")),
            EvalCase(
                id="judged",
                prompt="write well",
                expect=Expect(grade="judge", rubric="good"),
            ),
        ],
    )
    return SimpleNamespace(hub=hub, suite=suite, state=state)


async def _settle() -> None:
    await asyncio.gather(*list(fabric._active_remote_suites.values()))
    for _ in range(5):
        await asyncio.sleep(0)


def test_a_node_that_is_not_accepting_says_so(world):
    world.state["accepting"] = False

    async def go():
        with pytest.raises(fabric.OfferRefused, match="acceptRemoteSuites is off"):
            await fabric.offer_suite(RUNNER, world.suite.id)

    asyncio.run(go())
    (run,) = store.list_runs(world.suite.id)
    assert run.status == "cancelled"
    assert "acceptRemoteSuites is off" in run.error
    assert run.attestation == "peer"
    assert fabric._incoming == {}


def test_nothing_runs_until_a_person_accepts(world):
    async def go():
        out = await fabric.offer_suite(RUNNER, world.suite.id)
        # The judge case never left this node.
        assert [s["caseId"] for s in out["skipped"]] == ["judged"]
        run_id = out["run"]["id"]
        assert list(fabric._incoming) == [run_id]
        assert world.state["ran"] == []
        assert store.get_run(run_id).status == "queued"

        await fabric.accept(run_id, [{"name": "show"}])
        await _settle()
        return run_id

    run_id = asyncio.run(go())
    assert world.state["ran"] == [("c1", "qwen3:8b"), ("c2", "qwen3:8b")]

    run = store.get_run(run_id)
    assert run.status == "done"
    assert (run.passed, run.completed, run.total) == (1, 2, 2)
    assert run.model == "qwen3:8b" and run.harness_hash == "runnerhash01"
    assert run.attestation == "peer" and run.node == RUNNER

    results = {r.case_id: r for r in store.list_results(run_id)}
    suite_cases = {c.id: c for c in store.load_cases(world.suite)}
    for case_id, result in results.items():
        # The question is this node's to vouch for, never the runner's claim.
        assert result.case_hash == suite_cases[case_id].content_hash()
        assert result.turn_id == ""
        assert SECRET not in result.answer
        assert SECRET not in json.dumps([c.model_dump() for c in result.actual])
    assert fabric._incoming == {} and fabric._outgoing == {}


def test_the_runner_refuses_what_it_must_not_run_whatever_the_offerer_filtered(world):
    judged = EvalCase(id="j", prompt="p", expect=Expect(grade="judge", rubric="r"))

    async def go():
        return await world.hub.request(
            RUNNER,
            fabric.EVAL_OFFER,
            {"offerId": "raw1", "suiteName": "s", "cases": [judged.model_dump()]},
        )

    reply = asyncio.run(go())
    assert reply.type == fabric.EVAL_DECLINE
    assert "cannot run remotely" in reply.data["reason"]
    assert fabric._incoming == {}


def test_an_untrusted_node_gets_silence(world):
    world.hub.trusted = False
    case = EvalCase(id="c", prompt="p")

    async def go():
        await world.hub.request(
            RUNNER,
            fabric.EVAL_OFFER,
            {"offerId": "x", "suiteName": "s", "cases": [case.model_dump()]},
        )

    with pytest.raises(TimeoutError):
        asyncio.run(go())
    assert fabric._incoming == {}


def test_progress_from_another_node_is_not_a_result(world):
    async def go():
        out = await fabric.offer_suite(RUNNER, world.suite.id)
        run_id = out["run"]["id"]
        forged = PeerEnvelope(
            type=fabric.EVAL_PROGRESS,
            msg_id="f",
            src=STRANGER,
            ts=0.0,
            data={"offerId": run_id, "result": {"case_id": "c1", "passed": True}},
        )
        await fabric.handle_progress(world.hub, Session(STRANGER), forged)
        return run_id

    run_id = asyncio.run(go())
    assert store.list_results(run_id) == []


def test_done_with_cases_missing_is_not_done(world):
    async def go():
        out = await fabric.offer_suite(RUNNER, world.suite.id)
        run_id = out["run"]["id"]
        env = PeerEnvelope(
            type=fabric.EVAL_RESULT,
            msg_id="r",
            src=RUNNER,
            ts=0.0,
            data={"offerId": run_id, "status": "done"},
        )
        await fabric.handle_result(world.hub, Session(RUNNER), env)
        return run_id

    run = store.get_run(asyncio.run(go()))
    assert run.status == "failed"
    assert "never reported" in run.error


def test_withdrawing_a_pending_offer_clears_it_on_the_runner(world):
    async def go():
        out = await fabric.offer_suite(RUNNER, world.suite.id)
        run_id = out["run"]["id"]
        assert await fabric.withdraw(run_id)
        return run_id

    run_id = asyncio.run(go())
    assert fabric._incoming == {}
    assert store.get_run(run_id).status == "cancelled"


def test_accepting_needs_a_browser_catalog(world):
    async def go():
        out = await fabric.offer_suite(RUNNER, world.suite.id)
        with pytest.raises(ValueError, match="no browser"):
            await fabric.accept(out["run"]["id"], [])

    asyncio.run(go())
    assert world.state["ran"] == []


def test_compare_labels_a_peer_run_rather_than_calling_the_harness_the_same(world):
    local = store.create_run(world.suite.id, "local", "ollama", "", "qwen3:8b", 1)
    store.update_run(local.id, status="done", harness_hash="runnerhash01")

    async def go():
        out = await fabric.offer_suite(RUNNER, world.suite.id)
        await fabric.accept(out["run"]["id"], [{"name": "show"}])
        await _settle()
        return out["run"]["id"]

    peer_id = asyncio.run(go())
    diff = leaderboard.diff(local.id, peer_id)
    assert diff["harness"]["differs"] is False
    assert diff["harness"]["peerAttested"] is True
    board = leaderboard.build(world.suite.id)
    flags = {r["id"]: r["peerAttested"] for r in board["runs"]}
    assert flags == {local.id: False, peer_id: True}


def test_a_restart_closes_peer_runs_that_can_never_finish(world):
    run = store.create_run(world.suite.id, "x", "", "", "", 1, node=RUNNER)
    store.update_run(run.id, attestation="peer", status="running")
    fabric.close_stale_runs()
    assert store.get_run(run.id).status == "failed"
