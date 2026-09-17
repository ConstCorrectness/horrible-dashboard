"""HTTP surface for trajectories.

One ingest path (`POST /ingest`) is shared by the Python SDK, every importer and
each internal adapter, so there is exactly one place where a run becomes a row and
exactly one place to fix when the normalisation is wrong.
"""

from __future__ import annotations

import logging

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.modules.trajectories import analyze, export, search, store
from backend.modules.trajectories.adapters import games as games_adapter
from backend.modules.trajectories.adapters import importers
from backend.modules.trajectories.models import (
    CreateDataset,
    Dataset,
    DatasetListResponse,
    Harness,
    HarnessListResponse,
    IngestRequest,
    IngestResponse,
    LabelWrite,
    RunListResponse,
    TrajectoryDetail,
    TrajectoryLabel,
    UpdateDataset,
)

logger = logging.getLogger("trajectories")

router = APIRouter(prefix="/trajectories", tags=["trajectories"])


# --- datasets ---------------------------------------------------------------


@router.get("/datasets", response_model=DatasetListResponse)
async def list_datasets() -> DatasetListResponse:
    return DatasetListResponse(datasets=store.list_datasets())


@router.post("/datasets", response_model=Dataset)
async def create_dataset(body: CreateDataset) -> Dataset:
    if store.get_dataset(body.id) is not None:
        raise HTTPException(status_code=409, detail=f"dataset {body.id} already exists")
    return store.create_dataset(
        body.id,
        body.name,
        description=body.description,
        source_kind=body.source_kind,
        capture=body.capture,
        tags=body.tags,
    )


@router.get("/datasets/{dataset_id}", response_model=Dataset)
async def get_dataset(dataset_id: str) -> Dataset:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


@router.patch("/datasets/{dataset_id}", response_model=Dataset)
async def update_dataset(dataset_id: str, body: UpdateDataset) -> Dataset:
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    # At most one dataset captures at a time: two would make "where did this run
    # go" ambiguous, and the recorder picks one anyway.
    if body.capture:
        for other in store.list_datasets():
            if other.id != dataset_id and other.capture:
                store.update_dataset(other.id, capture=False)
    try:
        updated = store.update_dataset(
            dataset_id,
            name=body.name,
            description=body.description,
            capture=body.capture,
            shared=body.shared,
            tags=body.tags,
        )
    except store.SharingRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if body.shared is not None:
        await _announce_sharing_changed()
    return updated


async def _announce_sharing_changed() -> None:
    """Re-advertise this node's capabilities after what it shares changes.

    The `trajectories` capability carries `sharedDatasets`, computed when capabilities
    are *advertised* — at handshake. Without this, a friend connected before you
    shared anything is told "0 shared datasets" for as long as the link lasts, and
    their pane reads that as you sharing nothing. Found with two real nodes; the unit
    tests never advertise. `announce_presence` is the fabric's own mechanism for a
    provider whose answer changed, and it debounces.
    """
    try:
        from backend.modules.network.hub import peer_hub

        await peer_hub.announce_presence()
    except Exception:  # noqa: BLE001 - a stale count is not worth failing the toggle
        logger.debug("trajectories: presence announce failed", exc_info=True)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str) -> dict[str, bool]:
    was_shared = bool((ds := store.get_dataset(dataset_id)) and ds.shared)
    removed = store.delete_dataset(dataset_id)
    if was_shared:
        await _announce_sharing_changed()
    return {"ok": removed}


# --- runs -------------------------------------------------------------------


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    dataset: str | None = None,
    source: str | None = None,
    harness: str | None = None,
    outcome: str | None = None,
    agent: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> RunListResponse:
    runs, total = store.list_runs(
        dataset_id=dataset,
        source=source,
        harness=harness,
        outcome=outcome,
        agent_id=agent,
        status=status,
        q=q,
        limit=limit,
        offset=offset,
    )
    return RunListResponse(runs=runs, total=total)


@router.get("/runs/{run_id}", response_model=TrajectoryDetail)
async def get_run(run_id: str, steps: bool = True) -> TrajectoryDetail:
    run = store.get_run(run_id, with_steps=steps)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/runs/{run_id}/io")
async def run_io(run_id: str, round_no: int | None = Query(None, alias="round")):
    """The wire traffic this run produced, bucketed by round.

    Lives here rather than under `/telemetry` because the caller holds a run id and
    not a turn id; the join is `traj_runs.turn_id`, and making the pane do that
    lookup itself would put a schema detail in the browser. A run with no `turn_id`
    — anything imported or pushed in by the SDK — has no wire to show and says so,
    rather than returning an empty list that reads as "this run made no requests".
    """
    run = store.get_run(run_id, with_steps=False)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not run.turn_id:
        return {"events": [], "joinable": False}

    from backend.modules.telemetry import store as telemetry_store

    return {
        "events": telemetry_store.for_turn(run.turn_id, round_no=round_no),
        "joinable": True,
    }


def _pair_mcp(run: Any) -> dict[str, dict[str, Any]]:
    """Each MCP action step of a run, keyed by `seq`, paired with its call summary.

    A call row cannot carry its step: the step is written after the call returns. So
    the pairing is `(round, agent-facing tool name)` plus ordinal within that group —
    exact, because the orchestrator runs a round's calls strictly in sequence and both
    rows come from the one invocation. Names are compared through `bridge.tool_name`,
    which applies the same sanitizing the agent saw; comparing the raw MCP tool name
    to the step name would silently miss any tool whose name has a character providers
    reject. A step with no partner is omitted, not paired with a guess.
    """
    from backend.modules.mcp import bridge, calls

    buckets: dict[tuple[int | None, str], list[dict[str, Any]]] = {}
    for call in calls.list_calls(turn_id=run.turn_id, limit=2000):
        key = (call["round"], bridge.tool_name(call["server_id"], call["tool"]))
        buckets.setdefault(key, []).append(call)

    paired: dict[str, dict[str, Any]] = {}
    for step in run.step_list:
        if step.kind != "action" or not step.name:
            continue
        if bridge.split_tool_name(step.name) is None:
            continue
        queue = buckets.get((step.round, step.name))
        if queue:
            paired[str(step.seq)] = queue.pop(0)
    return paired


@router.get("/runs/{run_id}/mcp")
async def run_mcp(run_id: str) -> dict[str, Any]:
    """Every MCP step of a run with the call summary it produced. See `_pair_mcp`."""
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not run.turn_id:
        return {"calls": {}, "joinable": False}
    return {"calls": _pair_mcp(run), "joinable": True}


@router.get("/runs/{run_id}/mcp/{seq}/wire")
async def run_mcp_wire(run_id: str, seq: int) -> dict[str, Any]:
    """The JSON-RPC exchange behind one MCP step, if it is still in memory.

    The transcript is a deliberately small in-process ring — it is a debugging view
    and arguments often carry the user's own text — so for any call older than the
    last couple of hundred messages, or from before a restart, the wire is simply
    gone. `available: false` says exactly that. Returning an empty message list alone
    would read as "the call sent nothing", which is never true of a recorded call.
    """
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    call = _pair_mcp(run).get(str(seq)) if run.turn_id else None
    if call is None:
        raise HTTPException(
            status_code=404, detail="no MCP call recorded for this step"
        )

    from backend.modules.mcp import transcript

    ring = transcript.for_server(call["server_id"])
    messages = (
        [
            m.public()
            for m in ring.by_ids(call["rpc_ids"], session=call.get("session", ""))
        ]
        if call["rpc_ids"]
        else []
    )
    return {"call": call, "messages": messages, "available": bool(messages)}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str) -> dict[str, bool]:
    return {"ok": store.delete_run(run_id)}


@router.post("/runs/{run_id}/labels", response_model=TrajectoryLabel)
async def add_label(run_id: str, body: LabelWrite) -> TrajectoryLabel:
    if store.get_run(run_id, with_steps=False) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return store.add_label(run_id, body)


# --- ingest -----------------------------------------------------------------


def _ingest_all(writes: list[Any]) -> IngestResponse:
    """Write a batch and report it. Shared by `/ingest`, `/import` and the replay
    importer so there is one place a run becomes a row."""
    run_ids: list[str] = []
    created = 0
    for write in writes:
        run_id, is_new = store.ingest_run(write)
        run_ids.append(run_id)
        created += 1 if is_new else 0
    return IngestResponse(
        run_ids=run_ids, created=created, merged=len(run_ids) - created
    )


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    result = _ingest_all(body.runs)
    # Indexed here, awaited, because an ingest is already a bulk operation: one
    # whole-table merge for the whole batch. Failure is logged and dropped — a
    # missing index entry is a `POST /reindex` away, a rejected ingest is data
    # the caller may not still have.
    try:
        await search.index_runs(result.run_ids)
    except Exception:
        logger.debug("trajectories: post-ingest indexing failed", exc_info=True)
    return result


# --- friends ----------------------------------------------------------------
#
# The pulling and watching half of `fabric.py`. The roster join happens here,
# backend-side — the `hassault` invitees precedent — so the pane never imports
# across the social module's boundary.


def _trusted_peer(node_id: str) -> Any:
    """A connected, trusted peer, or 404.

    Checked here as well as on the friend's side: without it this node's routes would
    relay requests to any node it happens to be connected to, trusted or not.
    """
    from backend.modules.network.hub import peer_hub

    peer = next((p for p in peer_hub.list_peers() if p.node_id == node_id), None)
    if peer is None or not peer.trusted:
        raise HTTPException(status_code=404, detail="not a connected friend")
    return peer


def _refused(exc: Exception) -> HTTPException:
    # 502, not 500: this node worked; the friend's did not answer usefully.
    return HTTPException(status_code=502, detail=str(exc))


# --- commons ----------------------------------------------------------------


class PublishRequest(BaseModel):
    include_goal: bool = False
    #: The first characters of the previewed digest's content hash, typed by a person.
    confirm: str = ""


@router.get("/runs/{run_id}/commons-digest")
async def commons_digest(run_id: str, include_goal: bool = False) -> dict[str, Any]:
    """Exactly what `publish` would send, and the code that confirms it."""
    from backend.modules.trajectories import commons

    try:
        return commons.preview(run_id, include_goal=include_goal)
    except commons.PublishRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/publish")
async def publish_to_commons(run_id: str, req: PublishRequest) -> dict[str, Any]:
    from backend.modules.trajectories import commons

    try:
        return await commons.publish(
            run_id, include_goal=req.include_goal, confirm=req.confirm
        )
    except commons.PublishRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/commons")
async def commons_digests(mine: bool = False, limit: int = 50) -> dict[str, Any]:
    """Digests on the connected index. `connected: false` rather than an error when
    there is no index, so the section can say so."""
    from backend.modules.network.commons import commons_client
    from backend.modules.network.hub import peer_hub

    me = peer_hub.identity().node_id
    if not commons_client.connected:
        return {"connected": False, "me": me, "digests": []}
    try:
        digests = await commons_client.list_trajectories(me if mine else "", limit)
    except (ConnectionError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"index: {exc}") from exc
    return {"connected": True, "me": me, "digests": digests}


@router.delete("/commons/{digest_id}")
async def unpublish_from_commons(digest_id: str) -> dict[str, Any]:
    from backend.modules.network.commons import commons_client

    if not commons_client.connected:
        raise HTTPException(status_code=409, detail="not connected to a commons index")
    try:
        return {"ok": await commons_client.unpublish_trajectory(digest_id)}
    except (ConnectionError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"index: {exc}") from exc


@router.get("/peers")
async def list_peers() -> dict[str, Any]:
    """Online friends whose nodes can share trajectories, grouped by person."""
    from backend.modules.network.hub import peer_hub
    from backend.modules.social import roster
    from backend.modules.social import store as social_store
    from backend.modules.trajectories import fabric

    online = roster.online_nodes()
    by_node = {
        p.node_id: p
        for p in peer_hub.list_peers()
        if p.trusted and fabric.CAPABILITY in (p.capabilities or [])
    }
    people = []
    for friend in social_store.list_friends(online):
        if friend.status != "accepted" or friend.is_self:
            continue
        devices = []
        for device in friend.devices:
            peer = by_node.get(device.node_id)
            if not device.online or peer is None:
                continue
            cap = next((c for c in peer.caps if c.id == fabric.CAPABILITY), None)
            devices.append(
                {
                    "node_id": device.node_id,
                    "label": device.label or peer.node_name,
                    # None when the peer advertised the capability without attrs —
                    # an older build — which is different from "shares nothing".
                    "shared_datasets": (
                        cap.attrs.get("sharedDatasets") if cap else None
                    ),
                }
            )
        if devices:
            people.append(
                {
                    "person_id": friend.person_id,
                    "name": friend.display_name,
                    "devices": devices,
                }
            )
    return {"people": people}


@router.get("/peers/{node_id}/datasets")
async def peer_datasets(node_id: str) -> dict[str, Any]:
    from backend.modules.trajectories import fabric

    _trusted_peer(node_id)
    try:
        return {"datasets": await fabric.list_peer_datasets(node_id)}
    except fabric.PeerRefused as exc:
        raise _refused(exc) from exc


@router.get("/peers/{node_id}/runs")
async def peer_runs(
    node_id: str,
    dataset: str,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    from backend.modules.trajectories import fabric

    _trusted_peer(node_id)
    try:
        runs = await fabric.list_peer_runs(node_id, dataset, status=status, limit=limit)
    except fabric.PeerRefused as exc:
        raise _refused(exc) from exc
    return {"runs": runs}


@router.get("/peers/{node_id}/runs/{run_id}")
async def peer_run(node_id: str, run_id: str) -> dict[str, Any]:
    """A friend's run, fetched and **not stored** — for reading before pulling, and
    for a live watcher catching up on the steps it missed."""
    from backend.modules.trajectories import fabric

    _trusted_peer(node_id)
    try:
        return await fabric.fetch_peer_run(node_id, run_id)
    except fabric.PeerRefused as exc:
        raise _refused(exc) from exc


@router.post("/peers/{node_id}/runs/{run_id}/pull")
async def pull_peer_run(node_id: str, run_id: str) -> dict[str, str]:
    """Import a friend's finished run into `peer-<node>`. Re-pulling replaces it."""
    from backend.modules.trajectories import fabric

    _trusted_peer(node_id)
    try:
        return {"run_id": await fabric.pull_run(node_id, run_id)}
    except fabric.PeerRefused as exc:
        raise _refused(exc) from exc


@router.get("/watching")
async def watching() -> dict[str, Any]:
    """Share sessions this node has joined, whose hosts can stream runs live.

    Watching is authorized by the session, so a friend who has not invited you into
    one is not listed — being friends is reachability, not an invitation.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.share.session import share_manager
    from backend.modules.trajectories import fabric

    capable = {
        p.node_id
        for p in peer_hub.list_peers()
        if p.trusted and fabric.CAPABILITY in (p.capabilities or [])
    }
    return {
        "sessions": [
            {
                "session_id": s.id,
                "host": s.host_node,
                "host_name": s.host_name,
                "title": s.title,
                "grant": s.grant,
            }
            for s in share_manager.joined.values()
            if s.host_node in capable
        ]
    }


# --- harnesses --------------------------------------------------------------


@router.get("/harnesses", response_model=HarnessListResponse)
async def list_harnesses(limit: int = Query(100, ge=1, le=500)) -> HarnessListResponse:
    return HarnessListResponse(harnesses=store.list_harnesses(limit))


@router.get("/harnesses/{fingerprint}", response_model=Harness)
async def get_harness(fingerprint: str) -> Harness:
    harness = store.get_harness(fingerprint)
    if harness is None:
        raise HTTPException(status_code=404, detail="harness not found")
    return harness


# --- aggregates -------------------------------------------------------------


@router.get("/stats")
async def stats(dataset: str | None = None) -> dict[str, Any]:
    """Headline counts plus the top tools. Deliberately not `response_model`-typed:
    the shape is a dashboard payload, and pinning it here would mean editing two
    files every time a tile is added."""
    return analyze.dataset_stats(dataset)


@router.get("/tools")
async def tools(
    dataset: str | None = None,
    harness: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    return {
        "tools": analyze.tool_stats(dataset_id=dataset, harness=harness, limit=limit)
    }


@router.get("/compare")
async def compare(a: str, b: str) -> dict[str, Any]:
    """Compare two harnesses. Reports `comparable: false` when the two did not run
    on enough of the same goals for the difference to mean anything."""
    for fingerprint in (a, b):
        if store.get_harness(fingerprint) is None:
            raise HTTPException(status_code=404, detail=f"no harness {fingerprint}")
    return analyze.compare(a, b)


# --- search -----------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = ""
    dataset: str | None = None
    #: Successes only unless asked otherwise — retrieving failures as worked
    #: examples teaches the failure. Pass null to search everything.
    outcome: str | None = "success"
    harness: str | None = None
    limit: int = 5


@router.post("/search")
async def search_runs(body: SearchRequest) -> dict[str, Any]:
    """Semantic search over runs.

    `method` in the response says whether you got `semantic`, `substring` (no
    embedder answered) or `recent` (empty query) — a caller is told which it got
    rather than handed silently worse results.
    """
    runs, method = await search.search_runs(
        body.query,
        limit=max(1, min(body.limit, 50)),
        dataset_id=body.dataset,
        outcome=body.outcome,
        harness=body.harness,
    )
    return {"runs": [r.model_dump() for r in runs], "method": method}


@router.post("/reindex")
async def reindex(dataset: str | None = None, full: bool = False) -> dict[str, int]:
    """Build the vector index. `full` drops the collection and rebuilds."""
    return await search.reindex(dataset, full=full)


# --- export -----------------------------------------------------------------


class ExportRequest(BaseModel):
    name: str = "trajectories"
    dataset: str | None = None
    harness: str | None = None
    #: Require the outcome label to come from this source. `human` is the one to
    #: use when the training run matters — an `agent-critic` label is a model
    #: grading a model.
    label_source: str | None = None
    min_score: float | None = None
    limit: int = 1000


@router.post("/export")
async def export_sft(body: ExportRequest) -> dict[str, Any]:
    """Write an SFT JSONL file of graded successes. Reports what it skipped."""
    return export.export_dataset(
        name=body.name,
        dataset_id=body.dataset,
        harness=body.harness,
        label_source=body.label_source,
        min_score=body.min_score,
        limit=max(1, min(body.limit, 10000)),
    )


# --- import -----------------------------------------------------------------


class ImportRequest(BaseModel):
    dataset_id: str
    #: One of `importers.FORMATS`.
    format: str
    content: str


@router.post("/import", response_model=IngestResponse)
async def import_file(body: ImportRequest) -> IngestResponse:
    try:
        writes = importers.import_any(body.format, body.content, body.dataset_id)
    except importers.ImportFormatError as exc:
        # 400, not 500: the payload is wrong, not the server.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ingest_all(writes)


@router.post("/import/replay/{replay_id}", response_model=IngestResponse)
async def import_replay(replay_id: str, dataset_id: str = "games") -> IngestResponse:
    """Import a games replay — one run per seat."""
    from backend.modules.games import server_auth

    replay = await server_auth.replay_get(replay_id)
    if not replay or replay.get("error"):
        raise HTTPException(
            status_code=404, detail=str(replay.get("error") if replay else "no replay")
        )
    return _ingest_all(games_adapter.replay_to_writes(replay, dataset_id))
