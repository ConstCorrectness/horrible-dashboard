"""Trajectories across the peer fabric: list a friend's shared runs, pull one, watch live.

Wire types are declared **here, not in `network/protocol.py`** — the share and hassault
precedent. A module owns its own vocabulary, so this feature never edits the fabric core.

## Three gates, in order, none skippable

1. `session.info.trusted` — the envelope came from a friend. Reachability only.
2. The dataset is **shared** (`traj_datasets.shared`, default off) and is not itself
   a `peer` dataset. A run a friend shared with *you* is not yours to hand onward.
3. For live watching only: the friend is a participant in a share session this node
   is **hosting**, holding at least `view` — decided by `share.gate.require`, the one
   door every share permission goes through. There is no second, laxer check here.

A friend asking about a dataset that is unshared, missing, or peer-sourced gets the
same answer — "not shared" — so the reply does not confirm what exists.

## Everything leaving is redacted here, on this node

`outbound.redact` runs before the envelope is signed. The receiver is the wrong place
to hide anything, and `store.redact` is the wrong function (it misses `api_key` and
`Authorization`; see `outbound.py`). `turn_id`, `external_id`, `person_id`, `node_id`,
`parent_run_id` and `meta` are never sent: they are joins into this node's private
tables, identities, or raw frames.

## Replies are paged under a byte budget, because of who dialed whom

The side of a peer link that *dialed* runs a `websockets` client whose `max_size` is
1 MiB. One larger inbound message closes the entire link — chat, shared panes and all.
The accepting side is uvicorn, with a 16 MiB limit. So an unpaged "send the whole run"
would work for some friend pairs and silently sever others, depending only on which
of the two connected first. `TRAJ_FETCH` returns steps until `PEER_REPLY_MAX`, and
the puller asks again from `next_seq`.

## Live watching is its own content stream

The share mirror relays *layout* — which panes exist — and deliberately carries no pane
content (scratch uses the collab room for that). A guest rendering a mirrored
trajectories pane would see their own runs. So live runs get `TRAJ_LIVE`, while its
*authority* still comes entirely from the share session and its grant ladder.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from backend.modules.trajectories import outbound, store
from backend.modules.trajectories.models import (
    HarnessWrite,
    LabelWrite,
    StepWrite,
    TrajectoryStep,
    TrajectoryWrite,
)

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerCapability, PeerEnvelope

logger = logging.getLogger("trajectories")

CAPABILITY = "trajectories"

TRAJ_LIST = "traj_list"
TRAJ_FETCH = "traj_fetch"
TRAJ_LIVE = "traj_live"

#: Largest reply this node sends. Well under the 1 MiB `websockets` client default,
#: leaving room for the envelope, signature and JSON escaping of the payload.
PEER_REPLY_MAX = 512 * 1024

#: How long a puller waits for one page before giving up on the friend.
REQUEST_TIMEOUT_S = 15.0

#: A run served in more pages than this is refused on the pulling side. The byte
#: budget makes a real run need at most a few; a peer answering forever — bug or
#: malice — must not keep a route handler looping.
MAX_PAGES = 400

NOT_SHARED = "not shared"


class PeerRefused(Exception):
    """A friend's node declined, did not answer, or answered with something unusable."""


# --------------------------------------------------------------------------------
# What may be served
# --------------------------------------------------------------------------------


def _servable_dataset_ids() -> set[str]:
    return {d.id for d in store.list_datasets() if d.shared and d.source_kind != "peer"}


def _servable_run(run_id: str) -> Any:
    run = store.get_run(run_id, with_steps=False)
    if run is None or run.source == "peer":
        return None
    if run.dataset_id not in _servable_dataset_ids():
        return None
    return run


def _text(value: str | None) -> str:
    return outbound.scrub_text(value) if value else ""


def public_run(run: Any) -> dict[str, Any]:
    """A run header as a friend may see it. See the module docstring for what is
    withheld and why."""
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "source": run.source,
        "agent_id": run.agent_id,
        "agent_name": run.agent_name,
        "model": run.model,
        "provider": run.provider,
        "goal": _text(run.goal),
        "status": run.status,
        "outcome": run.outcome,
        "reward": run.reward,
        "steps": run.steps,
        "rounds": run.rounds,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "cost_usd": run.cost_usd,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "error": _text(run.error),
        "harness": run.harness,
    }


def public_step(step: TrajectoryStep | dict[str, Any]) -> dict[str, Any]:
    raw = step if isinstance(step, dict) else step.model_dump()
    return {
        "seq": raw.get("seq"),
        "kind": raw.get("kind"),
        "round": raw.get("round", 0),
        "role": raw.get("role"),
        "name": raw.get("name"),
        "args": outbound.bounded(raw.get("args")),
        "result": outbound.bounded(raw.get("result")),
        "ok": raw.get("ok"),
        "content": _text(raw.get("content")),
        "tokens": raw.get("tokens"),
        "duration_ms": raw.get("duration_ms"),
        "gated": bool(raw.get("gated")),
        "error": _text(raw.get("error")) or None,
        "ts": raw.get("ts"),
        "parent_seq": raw.get("parent_seq"),
    }


def _clip_text(text: str) -> str:
    """A long string cut to the per-payload budget, saying that it was cut."""
    if len(text) <= outbound.PEER_PAYLOAD_MAX:
        return text
    cut = len(text) - outbound.PEER_PAYLOAD_MAX
    return (
        text[: outbound.PEER_PAYLOAD_MAX]
        + f"\n…[{cut} characters withheld for sharing]"
    )


def public_harness(fingerprint: str | None) -> dict[str, Any] | None:
    """The harness, redacted and bounded.

    The system prompt is scrubbed and, if enormous, clipped with a note. That changes
    its fingerprint on the pulling side — which is correct: the text the puller holds
    is not the text that ran, and a comparison that pooled the two would be lying.
    """
    if not fingerprint:
        return None
    harness = store.get_harness(fingerprint)
    if harness is None:
        return None
    schemas = outbound.bounded(harness.tool_schemas)
    return {
        "agent_id": harness.agent_id,
        "model": harness.model,
        "provider": harness.provider,
        "system_prompt": _clip_text(_text(harness.system_prompt)),
        "tool_names": list(harness.tool_names),
        "tool_schemas": schemas
        if isinstance(schemas, dict) and "_omitted" not in schemas
        else {},
        "params": outbound.redact(harness.params),
        "label": harness.label,
    }


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str))


# --------------------------------------------------------------------------------
# Serving side
# --------------------------------------------------------------------------------


async def handle_list(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend asking what this node shares, or which runs one shared dataset holds.

    Untrusted peers get silence rather than an error: the shape of this node's data is
    not something to confirm to a stranger.
    """
    if not session.info.trusted:
        return
    # Off the event loop: these are synchronous sqlite reads, and an inline handler is
    # also head-of-line delay for every other message on this friend's link.
    reply = await asyncio.to_thread(build_list_reply, dict(env.data or {}))
    await hub.send_to(session.info.node_id, TRAJ_LIST, reply, re=env.msg_id)


def build_list_reply(data: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(data.get("dataset") or "")
    servable = _servable_dataset_ids()
    if not dataset_id:
        return {
            "datasets": [
                {
                    "id": d.id,
                    "name": d.name,
                    "description": _text(d.description),
                    "run_count": d.run_count,
                }
                for d in store.list_datasets()
                if d.id in servable
            ]
        }
    if dataset_id not in servable:
        return {"error": NOT_SHARED}
    status = str(data.get("status") or "") or None
    try:
        limit = max(1, min(100, int(data.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    runs, total = store.list_runs(dataset_id=dataset_id, status=status, limit=limit)
    return {"runs": [public_run(r) for r in runs if r.source != "peer"], "total": total}


def build_page(run_id: str, from_seq: int) -> dict[str, Any] | None:
    """One reply's worth of a run, or None when it may not be served.

    The first page carries the header, harness and labels; every page carries as many
    steps as fit in `PEER_REPLY_MAX`, and always at least one, so progress is
    guaranteed however large a single step is (each payload is already capped by
    `outbound.bounded`).
    """
    run = _servable_run(run_id)
    if run is None:
        return None
    page: dict[str, Any] = {"steps": [], "next_seq": None}
    used = 0
    if from_seq <= 0:
        detail = store.get_run(run_id, with_steps=False)
        page["run"] = public_run(run)
        page["harness"] = public_harness(run.harness)
        page["labels"] = [
            {
                "step_seq": label.step_seq,
                "key": label.key,
                "value": _text(label.value),
                "score": label.score,
                "source": label.source,
                "rationale": _text(label.rationale),
            }
            for label in (detail.labels if detail else [])
        ]
        used = _size(page)

    cursor = max(0, from_seq)
    while True:
        chunk = store.list_steps(run_id, from_seq=cursor, limit=50)
        if not chunk:
            return page
        for step in chunk:
            item = public_step(step)
            size = _size(item)
            if page["steps"] and used + size > PEER_REPLY_MAX:
                page["next_seq"] = step.seq
                return page
            page["steps"].append(item)
            used += size
        cursor = chunk[-1].seq + 1


async def handle_fetch(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    if not session.info.trusted:
        return
    data = env.data or {}
    run_id = str(data.get("run_id") or "")
    try:
        from_seq = int(data.get("from_seq") or 0)
    except (TypeError, ValueError):
        from_seq = 0
    # Up to `PEER_REPLY_MAX` of reads, blob reads and JSON encoding: off the loop.
    page = await asyncio.to_thread(build_page, run_id, from_seq) if run_id else None
    await hub.send_to(
        session.info.node_id,
        TRAJ_FETCH,
        page if page is not None else {"error": NOT_SHARED},
        re=env.msg_id,
    )


def _capability() -> PeerCapability:
    """Advertised always, with how much is shared — so a friend's pane can say "shares
    nothing" instead of confusing that with "is on a build without this feature"."""
    from backend.modules.network.models import PeerCapability

    try:
        shared = len(_servable_dataset_ids())
    except Exception:  # noqa: BLE001 - never fail a handshake over a count
        shared = 0
    return PeerCapability(id=CAPABILITY, version=1, attrs={"sharedDatasets": shared})


# --------------------------------------------------------------------------------
# Pulling side
# --------------------------------------------------------------------------------


async def _ask(node_id: str, msg_type: str, data: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.network.hub import peer_hub

    try:
        env = await peer_hub.request(node_id, msg_type, data, timeout=REQUEST_TIMEOUT_S)
    except KeyError as exc:
        raise PeerRefused("that friend is not connected") from exc
    except TimeoutError as exc:
        raise PeerRefused("that friend did not answer in time") from exc
    reply = env.data if isinstance(env.data, dict) else {}
    if reply.get("error"):
        raise PeerRefused(str(reply["error"])[:200])
    return reply


async def list_peer_datasets(node_id: str) -> list[dict[str, Any]]:
    reply = await _ask(node_id, TRAJ_LIST, {})
    rows = reply.get("datasets")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def list_peer_runs(
    node_id: str, dataset_id: str, *, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    reply = await _ask(
        node_id,
        TRAJ_LIST,
        {"dataset": dataset_id, "status": status or "", "limit": limit},
    )
    rows = reply.get("runs")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def fetch_peer_run(node_id: str, run_id: str) -> dict[str, Any]:
    """A whole run from a friend, assembled page by page. Not stored.

    The reply is **untrusted input**: `next_seq` must move strictly forward and the
    page count is capped, or a misbehaving peer could hold this loop open forever.
    """
    first = await _ask(node_id, TRAJ_FETCH, {"run_id": run_id, "from_seq": 0})
    if not isinstance(first.get("run"), dict):
        raise PeerRefused("that friend's node sent a malformed run")
    steps = list(first.get("steps") or [])
    cursor = first.get("next_seq")
    pages = 1
    while cursor is not None:
        if not isinstance(cursor, int) or (
            steps and cursor <= int(steps[-1].get("seq", -1))
        ):
            raise PeerRefused("that friend's node sent pages out of order")
        pages += 1
        if pages > MAX_PAGES:
            raise PeerRefused("that run is too large to pull")
        page = await _ask(node_id, TRAJ_FETCH, {"run_id": run_id, "from_seq": cursor})
        steps.extend(page.get("steps") or [])
        cursor = page.get("next_seq")
    return {
        "run": first["run"],
        "harness": first.get("harness"),
        "labels": first.get("labels") or [],
        "steps": steps,
    }


def _friend_name(node_id: str) -> str:
    """The roster's name for a friend, never a name their node claims for itself."""
    try:
        from backend.modules.social import store as social_store

        person = social_store.person_for_node(node_id)
        if person:
            row = social_store.get_friend_row(person)
            if row and row.get("display_name"):
                return str(row["display_name"])
    except Exception:  # noqa: BLE001
        pass
    return "a friend"


def peer_dataset_id(node_id: str) -> str:
    """Where a friend's runs land. One dataset per device, keyed by the authenticated
    node id — never by anything the friend's node chose to call itself."""
    return f"peer-{node_id.lower()}"[:64]


async def pull_run(node_id: str, run_id: str) -> str:
    """Import a friend's finished run. Returns the local run id.

    Re-pulling replaces rather than duplicates: `external_id` is `node:run`, and
    ingest is idempotent on `(dataset_id, external_id)`.

    A run still in flight is refused. Its snapshot would be sealed here as if it had
    finished, and a later re-pull would silently rewrite history the reader had
    already drawn conclusions from; watching it live is the honest way to see it.
    """
    fetched = await fetch_peer_run(node_id, run_id)
    header = fetched["run"]
    if header.get("status") == "running":
        raise PeerRefused(
            "that run is still in progress — watch it live, or pull it once it ends"
        )

    person_id = ""
    try:
        from backend.modules.social import store as social_store

        person_id = social_store.person_for_node(node_id) or ""
    except Exception:  # noqa: BLE001
        pass

    dataset_id = peer_dataset_id(node_id)
    # Validated in full *before* anything is written: a malformed reply used to leave
    # an empty `peer-…` dataset behind, created ahead of the payload it was for.
    try:
        harness = HarnessWrite(**fetched["harness"]) if fetched.get("harness") else None
        write = TrajectoryWrite(
            dataset_id=dataset_id,
            source="peer",
            external_id=f"{node_id}:{run_id}",
            harness=harness,
            agent_id=str(header.get("agent_id") or ""),
            agent_name=str(header.get("agent_name") or ""),
            model=str(header.get("model") or ""),
            provider=str(header.get("provider") or ""),
            goal=str(header.get("goal") or ""),
            status=header.get("status") or "complete",
            outcome=header.get("outcome"),
            reward=header.get("reward"),
            rounds=int(header.get("rounds") or 0),
            tokens_in=header.get("tokens_in"),
            tokens_out=header.get("tokens_out"),
            cost_usd=header.get("cost_usd"),
            started_at=header.get("started_at"),
            finished_at=header.get("finished_at"),
            error=str(header.get("error") or ""),
            # Provenance from the fabric, never from the payload.
            node_id=node_id,
            person_id=person_id,
            meta={"pulled_from_run": run_id},
            step_list=[StepWrite(**_step_write_fields(s)) for s in fetched["steps"]],
            labels=[],
        )
        labels = [LabelWrite(**_label_fields(lbl)) for lbl in fetched["labels"]]
    except Exception as exc:  # noqa: BLE001 - a friend's payload is untrusted input
        raise PeerRefused(
            f"that friend's node sent a run this node cannot read: {exc}"
        ) from exc

    return await asyncio.to_thread(_import_pulled, write, labels, node_id)


def _import_pulled(
    write: TrajectoryWrite, labels: list[LabelWrite], node_id: str
) -> str:
    """Write a validated pull. Synchronous sqlite, so the caller runs it off the loop."""
    if store.get_dataset(write.dataset_id) is None:
        store.create_dataset(
            write.dataset_id,
            f"From {_friend_name(node_id)}",
            description="Runs pulled from a friend's node. Cannot be shared onward.",
            source_kind="peer",
        )
    local_id, _ = store.ingest_run(write)
    # A re-pull replaces the steps (ingest is idempotent on `external_id`) but labels
    # are additive, so the friend's grades are cleared and re-applied rather than
    # appended — otherwise every re-pull would duplicate them. Only `import` labels:
    # a grade this node's own user added to the pulled run is theirs, and stays.
    with store.get_db_conn() as conn:
        conn.execute(
            "DELETE FROM traj_labels WHERE run_id = ? AND source = 'import'",
            (local_id,),
        )
    for label in labels:
        store.add_label(local_id, label)
    return local_id


_STEP_FIELDS = (
    "seq",
    "kind",
    "round",
    "role",
    "name",
    "args",
    "result",
    "ok",
    "content",
    "tokens",
    "duration_ms",
    "gated",
    "error",
    "ts",
    "parent_seq",
)


def _step_write_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: raw.get(k) for k in _STEP_FIELDS if k in raw}


def _label_fields(raw: dict[str, Any]) -> dict[str, Any]:
    fields = {
        k: raw.get(k)
        for k in ("step_seq", "key", "value", "score", "source", "rationale")
    }
    # A friend's grade is still a grade, but it is not *this* node's human judgment —
    # imported as `import` so it never averages with local human labels unnoticed.
    fields["source"] = "import"
    fields["value"] = fields.get("value") or ""
    fields["rationale"] = fields.get("rationale") or ""
    return fields


# --------------------------------------------------------------------------------
# Live watching — host side
# --------------------------------------------------------------------------------

#: Whether a run may be streamed, cached per run id. A run's dataset does not change
#: mid-run, and a step event must not cost a database read per watcher.
_watchable: dict[str, bool] = {}
_WATCHABLE_MAX = 512


def _is_watchable(run_id: str, *, refresh: bool = False) -> bool:
    if refresh or run_id not in _watchable:
        if len(_watchable) >= _WATCHABLE_MAX:
            _watchable.clear()
        _watchable[run_id] = _servable_run(run_id) is not None
    return _watchable[run_id]


def _watchers() -> list[str]:
    """Node ids allowed to watch right now: participants of the session this node hosts
    who pass `gate.require(..., "view")` — the share module's one door."""
    try:
        from backend.modules.share.gate import require
        from backend.modules.share.session import share_manager
    except Exception:  # noqa: BLE001
        return []
    hosting = share_manager.hosting
    if hosting is None:
        return []
    nodes = []
    for participant in hosting.participants:
        if participant.role == "host":
            continue
        ok, _ = require(participant, "view")
        if ok:
            nodes.append(participant.node_id)
    return nodes


def _live_payload(
    event: str, data: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """The run id an event concerns, and its public form — or None to send nothing."""
    if event == "run":
        run_id = str(data.get("id") or "")
        if not run_id or not _is_watchable(run_id, refresh=True):
            return None
        run = store.get_run(run_id, with_steps=False)
        return (run_id, public_run(run)) if run is not None else None
    run_id = str(data.get("runId") or "")
    if not run_id or not _is_watchable(run_id):
        return None
    if event == "step":
        step = dict(data.get("step") or {})
        return run_id, {
            "runId": run_id,
            "step": {
                **public_step(step),
                "args_bytes": step.get("args_bytes"),
                "result_bytes": step.get("result_bytes"),
            },
        }
    if event == "seal":
        return run_id, {
            k: data.get(k)
            for k in ("runId", "status", "steps", "rounds", "duration_ms")
        }
    return None


async def forward_live(event: str, data: dict[str, Any]) -> None:
    """Relay one local trajectory event to everyone currently allowed to watch.

    Called for every local event, so the common case — no session, or nobody in it —
    must cost nothing but a lookup, and a failure to reach one watcher must not stop
    the others.
    """
    watchers = _watchers()
    if not watchers:
        return
    try:
        # Off the loop: a `run` event reads the run back from sqlite.
        built = await asyncio.to_thread(_live_payload, event, data)
    except Exception:  # noqa: BLE001
        logger.debug("trajectories: live payload failed", exc_info=True)
        return
    if built is None:
        return
    _, payload = built
    from backend.modules.network.hub import peer_hub

    for node_id in watchers:
        try:
            await peer_hub.send_to(
                node_id, TRAJ_LIVE, {"event": event, "data": payload}
            )
        except Exception:  # noqa: BLE001 - one gone watcher must not silence the rest
            logger.debug("trajectories: could not reach watcher %s", node_id)


# --------------------------------------------------------------------------------
# Live watching — guest side
# --------------------------------------------------------------------------------


def _joined_host(node_id: str) -> Any:
    try:
        from backend.modules.share.session import share_manager
    except Exception:  # noqa: BLE001
        return None
    return next(
        (s for s in share_manager.joined.values() if s.host_node == node_id), None
    )


async def handle_live(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A host streaming one of their runs to us.

    Accepted only from the host of a share session this node has *joined*. Being a
    friend is not enough: a trusted peer that has not invited us into a session has no
    business pushing its agent's work onto our screen.
    """
    if not session.info.trusted:
        return
    joined = _joined_host(session.info.node_id)
    if joined is None:
        return
    data = env.data or {}
    event = str(data.get("event") or "")
    if event not in ("run", "step", "seal"):
        return
    from backend.modules.ws import broadcast_event

    await broadcast_event(
        "trajectories",
        "peer",
        {
            "host": session.info.node_id,
            # Share resolves this from the roster at join time (roster name first,
            # then the invite's label), so it is a name, not an id.
            "hostName": joined.host_name,
            "sessionId": joined.id,
            "event": event,
            "data": data.get("data") or {},
        },
    )


def register(hub: PeerHub) -> None:
    """Register the fabric handlers. Called from `network/setup.py`."""
    from backend.modules.network import capabilities

    hub.register_handler(TRAJ_LIST, handle_list)
    # Detached: a page is the heaviest thing this module does, and inline it would
    # hold every other message on the link — chat, shared panes — behind it.
    hub.register_handler(TRAJ_FETCH, handle_fetch, mode="detach")
    hub.register_handler(TRAJ_LIVE, handle_live)
    capabilities.register(CAPABILITY, _capability)
