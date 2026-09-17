"""Running your suite on a friend's agent — their node, their harness, their tokens.

Distinct from a **peer target** (`sweep._peer_endpoint`), which borrows a friend's
llama.cpp through a compute lease while grading, the harness and the catalog all stay
on this node. Here the friend's node runs the cases through *its own*
`run_agent_loop`, with its own skills, MCP servers and model, and reports back. That
is the only way to answer "how does their setup do on my suite" — and it is also why
none of it can be verified from here.

Wire types are declared **here**, not in `network/protocol.py` (the share/hassault
precedent). The flow:

    offerer                         runner
    EVAL_OFFER  ───────────────────▶  gates → reply EVAL_DECLINE (reason) | EVAL_PENDING
                                      a person accepts or declines
               ◀───────────────────  EVAL_ACCEPT (model, harness) | EVAL_DECLINE
               ◀───────────────────  EVAL_PROGRESS × cases
               ◀───────────────────  EVAL_RESULT (done | failed | cancelled)
    EVAL_CANCEL ───────────────────▶  (either side may stop it)

## Why this actuates only behind two gates

It spends the runner's tokens and GPU and runs their agent loop. So:

1. `evals.acceptRemoteSuites`, **default off**. The handler is registered regardless,
   so an offer to a node with it off is refused **with that reason** — silence would
   read as a network problem.
2. **A person accepts each offer**, having seen the case count and an estimate. There
   is no "always accept": `network.remoteAgentMode` answers a different question, and
   a mode that defaults to read-only means nothing for an N-case suite.

`session.info.trusted` comes first on every handler, as everywhere on the fabric.

## What the runner will not run

Only cases that `run_case` executes in-process with `simulate` — so **no tool acts on
the runner's machine**; every call returns the offerer's fixture. Refused outright,
on the runner (the offerer's filtering is a courtesy, never the gate):

- `hf_benchmark` cases, which build a venv and — for `code_exec` — execute
  model-written code;
- `judge` grades, which spend a second model call the runner never saw estimated,
  against a judge model the *offerer* names.

## Harness honesty

The runner reports `fingerprint.compute()` and its model name in `EVAL_ACCEPT`. The
envelope is signed, which proves **the node said it**, not that it is true — and the
hash covers skills and MCP, **not model weights**: a friend reporting `qwen3:8b` could
be running a fine-tune. So the run is stored with `attestation="peer"`, and Compare
labels it rather than presenting its harness as measured.

The offerer computes `case_hash` from **its own** case, never from what came back: the
question asked is the offerer's to vouch for.

## Sizes

An offer is capped (`OFFER_MAX`), and each progress message is bounded well under the
1 MiB frame that would close the dialing side's link (see
docs/modules/trajectories.mdx, "Replies are paged").
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.modules.evals import fingerprint, store
from backend.modules.evals.models import CaseResult, EvalCase, ToolCall

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

CAPABILITY = "evals-remote"

EVAL_OFFER = "eval_offer"
EVAL_PENDING = "eval_pending"
EVAL_ACCEPT = "eval_accept"
EVAL_DECLINE = "eval_decline"
EVAL_PROGRESS = "eval_progress"
EVAL_RESULT = "eval_result"
EVAL_CANCEL = "eval_cancel"

SETTING = "evals.acceptRemoteSuites"

#: Largest offer, serialized. Leaves the envelope far under the 1 MiB dialer frame.
OFFER_MAX = 512 * 1024
MAX_REMOTE_CASES = 200
#: Offers waiting on a person, across all friends. Beyond it, refuse — a queue of
#: offers nobody is looking at is a pile of work queued against a stranger's time.
MAX_PENDING = 5
#: An offer nobody answered in this long is declined as expired.
OFFER_TTL_S = 15 * 60
#: One progress message, serialized.
PROGRESS_MAX = 256 * 1024
ANSWER_MAX = 4000
DETAIL_MAX = 2000
#: Harness JSON is sent only below this; the hash is always sent.
HARNESS_JSON_MAX = 64 * 1024

NOT_ACCEPTING = (
    "this node is not accepting remote suites (evals.acceptRemoteSuites is off)"
)


# --------------------------------------------------------------------------------
# What may run remotely
# --------------------------------------------------------------------------------


def ineligible_reason(case: EvalCase) -> str | None:
    """Why a case cannot run on somebody else's node, or None when it can."""
    if case.type == "hf_benchmark" or case.benchmark is not None:
        return "benchmark cases build a venv and may execute model-written code"
    if case.expect.grade == "judge":
        return "judge grades spend a second, unestimated model call"
    return None


def estimate_tokens(cases: list[EvalCase]) -> int:
    """A floor on input tokens: the case text alone, at ~4 characters a token.

    A floor and labelled as one. Every round also resends the system prompt and the
    tool catalog, which on the runner's side this node cannot see.
    """
    chars = 0
    for case in cases:
        chars += len(case.prompt)
        chars += len(json.dumps(case.history)) if case.history else 0
        chars += len(json.dumps(case.fixtures)) if case.fixtures else 0
    return chars // 4


def _clip(text: str, limit: int) -> str:
    from backend.modules.trajectories import outbound

    text = outbound.scrub_text(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def public_result(result: CaseResult) -> dict[str, Any]:
    """A case result as it may leave the runner.

    The answer and the call arguments are the runner's model talking, and it saw the
    runner's skills — so both are scrubbed and bounded. `turn_id` joins into the
    runner's private tables and is dropped; `expected` and `case_hash` are the
    offerer's own and are not sent back.
    """
    from backend.modules.trajectories import outbound

    return {
        "case_id": result.case_id,
        "passed": bool(result.passed),
        "grade": result.grade,
        "detail": _clip(result.detail, DETAIL_MAX),
        "actual": [
            {"name": c.name, "arguments": outbound.bounded(c.arguments)}
            for c in result.actual
        ],
        "answer": _clip(result.answer, ANSWER_MAX),
        "rounds": result.rounds,
        "tools_offered": result.tools_offered,
        "tools_dropped": list(result.tools_dropped),
        "groups_loaded": list(result.groups_loaded),
        "duration_ms": result.duration_ms,
        "error": _clip(result.error, DETAIL_MAX),
    }


def _fit(payload: dict[str, Any]) -> dict[str, Any]:
    """Shrink a progress payload until it fits, dropping the bulkiest parts first."""
    if len(json.dumps(payload, default=str)) <= PROGRESS_MAX:
        return payload
    result = dict(payload["result"])
    result["actual"] = [{"name": c["name"], "arguments": {}} for c in result["actual"]]
    result["answer"] = result["answer"][:500] + "…"
    return {**payload, "result": result}


# --------------------------------------------------------------------------------
# Runner side
# --------------------------------------------------------------------------------


@dataclass
class IncomingOffer:
    offer_id: str
    from_node: str
    from_name: str
    suite_name: str
    cases: list[EvalCase]
    estimate_tokens: int
    received_at: float
    expires_at: float
    state: str = "pending"  # pending | running
    model: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "offerId": self.offer_id,
            "fromNode": self.from_node,
            "fromName": self.from_name,
            "suiteName": self.suite_name,
            "cases": len(self.cases),
            "estimateTokens": self.estimate_tokens,
            "estimateCostUsd": _estimate_cost(self.estimate_tokens),
            "receivedAt": self.received_at,
            "expiresAt": self.expires_at,
            "state": self.state,
            "model": self.model,
        }


_incoming: dict[str, IncomingOffer] = {}
#: The cancellable task per accepted offer, so EVAL_CANCEL has a handle.
_active_remote_suites: dict[str, asyncio.Task[Any]] = {}


def accepting() -> bool:
    try:
        from backend.modules.settings.routes import get_value

        return bool(get_value(SETTING, False))
    except Exception:  # noqa: BLE001
        return False


def _friend_name(node_id: str) -> str:
    """The roster's name for a friend; never what their node calls itself."""
    try:
        from backend.modules.social import store as social_store

        person = social_store.person_for_node(node_id)
        row = social_store.get_friend_row(person) if person else None
        if row and row.get("display_name"):
            return str(row["display_name"])
    except Exception:  # noqa: BLE001
        pass
    return "a friend"


def _local_target() -> tuple[Any, str, str]:
    """`(provider info, endpoint, model)` this node's agent answers with."""
    from backend.modules.agent.roster import resolve_provider
    from backend.modules.agent.routes import _load_config

    config = _load_config()
    if config is None:
        raise RuntimeError("no agent provider is configured on this node")
    info, endpoint = resolve_provider(config, "main")
    return info, endpoint, config.model


def _estimate_cost(tokens: int) -> float | None:
    """A floor in dollars for the input tokens, `0.0` on a local model, None unknown."""
    try:
        from backend.modules.agent import cost

        info, _endpoint, model = _local_target()
        if info.kind in cost.LOCAL_KINDS:
            return 0.0
        return cost.estimate(model, tokens, 0)
    except Exception:  # noqa: BLE001
        return None


def _prune_expired() -> list[IncomingOffer]:
    now = time.time()
    gone = [
        o for o in _incoming.values() if o.state == "pending" and o.expires_at < now
    ]
    for offer in gone:
        _incoming.pop(offer.offer_id, None)
    return gone


async def _emit(event: str, data: dict[str, Any]) -> None:
    try:
        from backend.modules.ws import broadcast_event

        await broadcast_event("evals", event, data)
    except Exception:  # noqa: BLE001
        logger.debug("evals: remote broadcast failed", exc_info=True)


async def _send(node_id: str, msg_type: str, data: dict[str, Any]) -> None:
    from backend.modules.network.hub import peer_hub

    try:
        await peer_hub.send_to(node_id, msg_type, data)
    except Exception:  # noqa: BLE001 - the other side is gone; nothing to tell it
        logger.debug("evals: could not reach %s with %s", node_id, msg_type)


def list_incoming() -> list[dict[str, Any]]:
    for offer in _prune_expired():
        asyncio.ensure_future(
            _send(
                offer.from_node,
                EVAL_DECLINE,
                {"offerId": offer.offer_id, "reason": "the offer expired unanswered"},
            )
        )
    return [o.public() for o in _incoming.values()]


async def handle_offer(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    if not session.info.trusted:
        return
    data = env.data or {}
    offer_id = str(data.get("offerId") or "")[:64]

    async def decline(reason: str) -> None:
        await hub.send_to(
            session.info.node_id,
            EVAL_DECLINE,
            {"offerId": offer_id, "reason": reason},
            re=env.msg_id,
        )

    if not offer_id:
        await decline("malformed offer")
        return
    if not accepting():
        await decline(NOT_ACCEPTING)
        return
    if len(json.dumps(data, default=str)) > OFFER_MAX:
        await decline("the offer is too large")
        return
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        await decline("the offer carries no cases")
        return
    if len(raw_cases) > MAX_REMOTE_CASES:
        await decline(f"more than {MAX_REMOTE_CASES} cases")
        return
    try:
        cases = [EvalCase.model_validate(c) for c in raw_cases]
    except Exception as exc:  # noqa: BLE001
        await decline(f"a case did not parse on this node: {str(exc)[:200]}")
        return
    for case in cases:
        reason = ineligible_reason(case)
        if reason:
            await decline(f"case {case.id!r} cannot run remotely: {reason}")
            return
    _prune_expired()
    if offer_id in _incoming:
        await decline("an offer with that id is already here")
        return
    if sum(1 for o in _incoming.values() if o.state == "pending") >= MAX_PENDING:
        await decline("too many offers are already waiting on this node")
        return

    now = time.time()
    offer = IncomingOffer(
        offer_id=offer_id,
        from_node=session.info.node_id,
        from_name=_friend_name(session.info.node_id),
        suite_name=str(data.get("suiteName") or "a suite")[:80],
        cases=cases,
        estimate_tokens=estimate_tokens(cases),
        received_at=now,
        expires_at=now + OFFER_TTL_S,
    )
    _incoming[offer_id] = offer
    await hub.send_to(
        session.info.node_id, EVAL_PENDING, {"offerId": offer_id}, re=env.msg_id
    )
    await _emit("remote_offer", offer.public())
    await _notify_offer(offer)


async def _notify_offer(offer: IncomingOffer) -> None:
    try:
        from backend.modules.notifications import service
        from backend.modules.social import store as social_store

        # `invite`: the closed category vocabulary is what lets a person mute it, and
        # an offer to run work on your machine is an invitation. `person_id` is what a
        # per-person mute matches on.
        await service.notify(
            "invite",
            f"{offer.from_name} wants to run a suite on your agent",
            f"{offer.suite_name} · {len(offer.cases)} cases · "
            f"at least ~{offer.estimate_tokens:,} input tokens",
            person_id=social_store.person_for_node(offer.from_node),
            kind="info",
            data={"dedupe": f"evals-offer:{offer.from_node}:{offer.offer_id}"},
        )
    except Exception:  # noqa: BLE001
        logger.debug("evals: could not notify about an offer", exc_info=True)


async def accept(offer_id: str, agent_tools: list[dict[str, Any]]) -> dict[str, Any]:
    """A person said yes. Starts the run and returns what was reported back."""
    offer = _incoming.get(offer_id)
    if offer is None or offer.state != "pending":
        raise ValueError("no such pending offer")
    if not accepting():
        raise ValueError(NOT_ACCEPTING)
    if not agent_tools:
        raise ValueError(
            "no browser is connected, so the frontend tool catalog is empty"
        )
    info, endpoint, model = _local_target()
    harness_hash, harness_json = fingerprint.compute()
    offer.state = "running"
    offer.model = model

    await _send(
        offer.from_node,
        EVAL_ACCEPT,
        {
            "offerId": offer_id,
            "provider": info.kind,
            "model": model,
            "harnessHash": harness_hash,
            "harnessJson": harness_json
            if len(harness_json) <= HARNESS_JSON_MAX
            else "",
        },
    )
    task = asyncio.create_task(_run_offer(offer, agent_tools, info, endpoint, model))
    _active_remote_suites[offer_id] = task
    task.add_done_callback(lambda _t: _active_remote_suites.pop(offer_id, None))
    await _emit("remote_offer", offer.public())
    return offer.public()


async def decline(offer_id: str, reason: str = "declined") -> None:
    offer = _incoming.pop(offer_id, None)
    if offer is None:
        return
    await _send(
        offer.from_node,
        EVAL_DECLINE,
        {"offerId": offer_id, "reason": reason[:200] or "declined"},
    )
    await _emit("remote_offer_closed", {"offerId": offer_id})


def stop(offer_id: str) -> bool:
    """Stop a remote suite this node is running."""
    task = _active_remote_suites.get(offer_id)
    if task is None:
        return False
    task.cancel()
    return True


async def _run_offer(
    offer: IncomingOffer,
    agent_tools: list[dict[str, Any]],
    info: Any,
    endpoint: str,
    model: str,
) -> None:
    from backend.modules.evals import sweep
    from backend.modules.evals.runner_agent import run_case

    status, error = "done", ""
    try:
        # The same semaphore as a local sweep: this node's model server is one
        # resource, and a friend's suite must queue behind the owner's, not beside it.
        async with sweep._target_semaphore:
            for index, case in enumerate(offer.cases):
                result = await run_case(
                    case, agent_tools, provider=info, endpoint=endpoint, model=model
                )
                await _send(
                    offer.from_node,
                    EVAL_PROGRESS,
                    _fit(
                        {
                            "offerId": offer.offer_id,
                            "index": index,
                            "total": len(offer.cases),
                            "result": public_result(result),
                        }
                    ),
                )
                await _emit(
                    "remote_progress",
                    {
                        "offerId": offer.offer_id,
                        "index": index,
                        "total": len(offer.cases),
                    },
                )
    except asyncio.CancelledError:
        status, error = "cancelled", "stopped on the runner's node"
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("evals: remote suite failed")
        status, error = "failed", str(exc)[:DETAIL_MAX]
    finally:
        _incoming.pop(offer.offer_id, None)
        # Detached: in the cancelled branch this coroutine is already cancelling.
        asyncio.ensure_future(
            _send(
                offer.from_node,
                EVAL_RESULT,
                {"offerId": offer.offer_id, "status": status, "error": error},
            )
        )
        asyncio.ensure_future(
            _emit("remote_offer_closed", {"offerId": offer.offer_id, "status": status})
        )


async def handle_cancel(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """The offerer withdrew. Only the node that made the offer may."""
    if not session.info.trusted:
        return
    offer_id = str((env.data or {}).get("offerId") or "")
    offer = _incoming.get(offer_id)
    if offer is None or offer.from_node != session.info.node_id:
        return
    if not stop(offer_id):
        _incoming.pop(offer_id, None)
        await _emit("remote_offer_closed", {"offerId": offer_id})


# --------------------------------------------------------------------------------
# Offerer side
# --------------------------------------------------------------------------------


@dataclass
class OutgoingOffer:
    run_id: str
    node_id: str
    friend: str
    cases: dict[str, EvalCase] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)


#: run id (= offer id) → the offer, while it can still receive messages.
_outgoing: dict[str, OutgoingOffer] = {}


class OfferRefused(Exception):
    """Could not be offered, or the friend's node refused; the message says why."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def offer_suite(
    node_id: str, suite_id: str, case_ids: list[str] | None = None
) -> dict[str, Any]:
    """Offer a suite to a friend's node. Returns the run row and what was left out."""
    from backend.modules.network.hub import peer_hub

    peer = peer_hub.peers.get(node_id) if hasattr(peer_hub, "peers") else None
    if peer is None or not getattr(getattr(peer, "info", None), "trusted", False):
        raise OfferRefused("that friend is not connected")
    suite = store.get_suite(suite_id)
    if suite is None:
        raise OfferRefused(f"no suite {suite_id!r}")
    cases = store.load_cases(suite)
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
    skipped = [
        {"caseId": c.id, "reason": r} for c in cases if (r := ineligible_reason(c))
    ]
    runnable = [c for c in cases if not ineligible_reason(c)]
    if not runnable:
        raise OfferRefused("none of these cases can run on another node")
    if len(runnable) > MAX_REMOTE_CASES:
        raise OfferRefused(f"more than {MAX_REMOTE_CASES} cases; filter the suite")
    payload_cases = [c.model_dump() for c in runnable]
    if len(json.dumps(payload_cases, default=str)) > OFFER_MAX - 4096:
        raise OfferRefused("the suite is too large to offer; filter the cases")

    friend = _friend_name(node_id)
    run = store.create_run(
        suite_id=suite_id,
        label=f"{friend}'s agent",
        provider="",
        endpoint="",
        model="",
        total=len(runnable),
        node=node_id,
    )
    store.update_run(run.id, attestation="peer")
    _outgoing[run.id] = OutgoingOffer(
        run_id=run.id,
        node_id=node_id,
        friend=friend,
        cases={c.id: c for c in runnable},
    )
    try:
        reply = await peer_hub.request(
            node_id,
            EVAL_OFFER,
            {"offerId": run.id, "suiteName": suite.name, "cases": payload_cases},
        )
    except Exception as exc:  # noqa: BLE001
        _close(run.id, "failed", f"could not reach {friend}: {exc or 'no answer'}")
        raise OfferRefused(f"could not reach {friend}") from exc
    if reply.type == EVAL_DECLINE:
        reason = str((reply.data or {}).get("reason") or "declined")[:200]
        _close(run.id, "cancelled", f"{friend} declined: {reason}")
        raise OfferRefused(f"{friend} declined: {reason}")
    await _emit("run_started", (store.get_run(run.id) or run).model_dump())
    return {"run": (store.get_run(run.id) or run).model_dump(), "skipped": skipped}


def _close(run_id: str, status: str, error: str = "") -> None:
    _outgoing.pop(run_id, None)
    store.update_run(run_id, status=status, error=error, finished_at=_now())


def _mine(session: PeerSession, env: PeerEnvelope) -> OutgoingOffer | None:
    """The offer this message is about — only if it came from the node we asked."""
    if not session.info.trusted:
        return None
    offer = _outgoing.get(str((env.data or {}).get("offerId") or ""))
    if offer is None or offer.node_id != session.info.node_id:
        return None
    return offer


async def handle_accept(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    offer = _mine(session, env)
    if offer is None:
        return
    data = env.data or {}
    model = str(data.get("model") or "")[:120]
    harness_json = str(data.get("harnessJson") or "")
    if len(harness_json) > HARNESS_JSON_MAX:
        harness_json = ""
    store.update_run(
        offer.run_id,
        status="running",
        provider=str(data.get("provider") or "")[:40],
        model=model,
        label=f"{model or 'agent'} · {offer.friend}",
        harness_hash=str(data.get("harnessHash") or "")[:64],
        harness_json=harness_json,
    )
    run = store.get_run(offer.run_id)
    if run is not None:
        await _emit("run_started", run.model_dump())


async def handle_decline(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    offer = _mine(session, env)
    if offer is None:
        return
    reason = str((env.data or {}).get("reason") or "declined")[:200]
    _close(offer.run_id, "cancelled", f"{offer.friend} declined: {reason}")
    await _emit("run_failed", {"runId": offer.run_id, "error": reason})


async def handle_progress(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    offer = _mine(session, env)
    if offer is None:
        return
    data = env.data or {}
    raw = data.get("result")
    if not isinstance(raw, dict):
        return
    case = offer.cases.get(str(raw.get("case_id") or ""))
    # A case we did not offer, or one already reported, is not a result.
    if case is None or case.id in offer.seen:
        return
    try:
        result = CaseResult(
            case_id=case.id,
            passed=bool(raw.get("passed")),
            grade=case.expect.grade,
            detail=str(raw.get("detail") or "")[:DETAIL_MAX],
            expected=list(case.expect.calls),
            actual=[
                ToolCall(
                    name=str(c.get("name") or ""),
                    arguments=c.get("arguments")
                    if isinstance(c.get("arguments"), dict)
                    else {},
                )
                for c in raw.get("actual") or []
                if isinstance(c, dict)
            ],
            answer=str(raw.get("answer") or "")[: ANSWER_MAX + 1],
            rounds=int(raw.get("rounds") or 0),
            tools_offered=int(raw.get("tools_offered") or 0),
            tools_dropped=[str(t) for t in raw.get("tools_dropped") or []][:200],
            groups_loaded=[str(g) for g in raw.get("groups_loaded") or []][:200],
            duration_ms=float(raw.get("duration_ms") or 0.0),
            error=str(raw.get("error") or "")[:DETAIL_MAX],
        )
    except (TypeError, ValueError):
        return
    if result.error:
        result.passed = False
    # Ours to vouch for: the question this node asked.
    result.case_hash = case.content_hash()
    offer.seen.add(case.id)
    store.save_result(offer.run_id, result)
    await _emit(
        "case_done",
        {
            "runId": offer.run_id,
            "index": len(offer.seen) - 1,
            "total": len(offer.cases),
            "result": result.model_dump(),
        },
    )


async def handle_result(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    offer = _mine(session, env)
    if offer is None:
        return
    data = env.data or {}
    status = str(data.get("status") or "failed")
    if status not in ("done", "failed", "cancelled"):
        status = "failed"
    error = str(data.get("error") or "")[:DETAIL_MAX]
    if status == "done" and len(offer.seen) < len(offer.cases):
        # "Done" with cases missing is not done: a run that silently answered half
        # the suite must not rank beside one that answered all of it.
        status = "failed"
        error = f"{len(offer.cases) - len(offer.seen)} case(s) never reported"
    _close(offer.run_id, status, error)
    run = store.get_run(offer.run_id)
    if run is not None:
        await _emit("run_done" if status == "done" else "run_failed", run.model_dump())


async def withdraw(run_id: str) -> bool:
    """Stop a suite this node offered, pending or running."""
    offer = _outgoing.get(run_id)
    if offer is None:
        return False
    await _send(offer.node_id, EVAL_CANCEL, {"offerId": run_id})
    _close(run_id, "cancelled", "withdrawn")
    await _emit("run_failed", {"runId": run_id, "error": "withdrawn"})
    return True


def close_stale_runs() -> None:
    """Peer runs left open by a restart can never finish: the offer lived in memory."""
    try:
        for run in store.list_runs(None, limit=500):
            if run.attestation == "peer" and run.status in ("queued", "running"):
                store.update_run(
                    run.id,
                    status="failed",
                    error="this node restarted while the friend's run was open",
                    finished_at=_now(),
                )
    except Exception:  # noqa: BLE001
        logger.debug("evals: could not close stale peer runs", exc_info=True)


# --------------------------------------------------------------------------------


def register(hub: PeerHub) -> None:
    """Register handlers. Called from `network/setup.py`, unconditionally — an offer
    to a node with the setting off must be refused with a reason, not met with
    silence."""
    from backend.modules.network import capabilities

    hub.register_handler(EVAL_OFFER, handle_offer)
    hub.register_handler(EVAL_ACCEPT, handle_accept)
    hub.register_handler(EVAL_DECLINE, handle_decline)
    hub.register_handler(EVAL_PROGRESS, handle_progress)
    hub.register_handler(EVAL_RESULT, handle_result)
    hub.register_handler(EVAL_CANCEL, handle_cancel)
    # Advertised with no live detail on purpose: "accepting" is a setting that
    # changes after the handshake, and a stale capability would be worse than an
    # offer refused with its reason.
    capabilities.register_static(CAPABILITY)
    close_stale_runs()
