"""Standalone **commons** server: the federatable *agent commons* index — public,
signed profiles plus semantic (vectordb) matchmaking — bundling the lobby + relay so
one process gives both discovery and connectivity.

Where the [lobby](lobby_server.py) is a bare presence directory + rooms, the commons is
a **marketplace for strangers**: nodes publish a rich `CommonsProfile` (a superset of an
A2A Agent Card), and others discover them by cosine-similarity search over the
[vectordb](../vectordb/database.py). Profiles are self-signed (Ed25519), so the index
verifies but cannot forge them — and a federated/DHT index could re-serve them later.

This is **Phase 1** of docs/architecture/agent-commons.mdx (profiles + search). The
consent handshake, reputation, and the node-side `CommonsClient` are later phases.

Run separately from a node's own backend:

    uv run uvicorn backend.modules.network.commons_server:app --port 9100

See docs/architecture/agent-commons.mdx.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.modules.network import identity, lobby_server, relay_broker
from backend.modules.network.models import (
    CommonsCandidate,
    CommonsProfile,
    canonical_profile_bytes,
)
from backend.modules.vectordb.database import (
    delete_document,
    init_db,
    search_documents,
    upsert_document,
)
from backend.modules.vectordb.embeddings import get_embedding

logger = logging.getLogger(__name__)

# The vectordb collection profiles are embedded into for matchmaking.
PROFILE_COLLECTION = "commons-profiles"


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def _profiles_path() -> Path:
    return _data_dir() / "commons-profiles.json"


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Embeddings live in the vectordb's SQLite store; profile metadata is mirrored to a
    # JSON file so the directory survives a restart (entries load back as offline until
    # their node reconnects).
    init_db()
    load_profiles()
    yield


app = FastAPI(title="horrible-dashboard commons", lifespan=_lifespan)

# Bundle connectivity the way lobby_server bundles the relay: the relay broker (data
# fallback) and the full lobby (directory/rooms/signaling) share this process, so one
# host serves both "find someone" and "reach someone".
app.add_api_websocket_route("/relay-ws", relay_broker.relay_ws)
app.add_api_websocket_route("/lobby-ws", lobby_server.lobby_ws)


class _Entry:
    """A known profile plus its live connection state (the connection itself is not
    part of the signed `CommonsProfile`)."""

    def __init__(
        self,
        profile: CommonsProfile,
        ws: WebSocket | None = None,
        addresses: list[str] | None = None,
    ) -> None:
        self.profile = profile
        self.ws = ws
        self.addresses = addresses or []
        self.status = "connected" if ws is not None else "disconnected"


# node_id -> entry. Profiles persist (offline) until explicitly unpublished.
_profiles: dict[str, _Entry] = {}

# request_id -> (requester_node_id, target_node_id): in-flight meet requests awaiting
# the target's accept/decline. The index only brokers consent; it never auto-connects.
_pending: dict[str, tuple[str, str]] = {}


# ---- helpers ----------------------------------------------------------------------


def _profile_text(p: CommonsProfile) -> str:
    """The text a profile is embedded as — what matchmaking searches over."""
    parts = [
        p.display_name,
        p.headline,
        p.bio or "",
        " ".join(p.tags),
        " ".join(p.agent_capabilities),
        p.seeking or "",
    ]
    return "\n".join(s for s in parts if s)


def verify_profile(profile: CommonsProfile) -> bool:
    """A profile is trusted iff it is signed by the key whose fingerprint is its
    `node_id` (self-certifying — the index can't be tricked into listing a profile
    under someone else's identity)."""
    if not profile.sig:
        return False
    if identity.fingerprint(profile.public_key) != profile.node_id:
        return False
    return identity.verify(
        profile.public_key, canonical_profile_bytes(profile), profile.sig
    )


def save_profiles() -> None:
    path = _profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.profile.model_dump() for e in _profiles.values()]
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_profiles() -> None:
    """Replace the in-memory registry from disk (entries start offline)."""
    _profiles.clear()
    path = _profiles_path()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        logger.warning("commons profiles file is corrupt; starting empty")
        return
    for item in raw:
        try:
            profile = CommonsProfile.model_validate(item)
        except Exception:
            continue
        _profiles[profile.node_id] = _Entry(profile)


def _directory() -> list[dict[str, Any]]:
    return [
        {**e.profile.model_dump(), "status": e.status}
        for e in _profiles.values()
        if e.profile.visibility == "public"
    ]


async def _send(ws: WebSocket, message: dict[str, Any]) -> None:
    try:
        await ws.send_text(json.dumps(message))
    except Exception:
        pass


def _drop(node_id: str) -> None:
    _profiles.pop(node_id, None)
    delete_document(node_id)
    save_profiles()


def _mark_offline(node_id: str) -> None:
    entry = _profiles.get(node_id)
    if entry is not None:
        entry.ws = None
        entry.status = "disconnected"


# ---- endpoints --------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "profiles": len(_profiles)}


@app.websocket("/commons-ws")
async def commons_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    node_id: str | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mtype = msg.get("type")

            if mtype in ("publish_profile", "register"):
                published = await _handle_publish(websocket, msg)
                if published is not None:
                    node_id = published
            elif mtype == "search":
                await _handle_search(websocket, msg)
            elif mtype == "directory":
                await _send(websocket, {"type": "directory", "profiles": _directory()})
            elif mtype == "unpublish":
                if node_id is not None:
                    _drop(node_id)
                    node_id = None
                await _send(websocket, {"type": "unpublished", "ok": True})
            elif mtype == "connect_request":
                if node_id is not None:
                    await _handle_connect_request(node_id, msg)
            elif mtype == "connect_response":
                if node_id is not None:
                    await _handle_connect_response(node_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        # Disconnect keeps the profile (now offline); only `unpublish` removes it.
        if node_id is not None:
            _mark_offline(node_id)


async def _handle_publish(ws: WebSocket, msg: dict[str, Any]) -> str | None:
    try:
        profile = CommonsProfile.model_validate(msg.get("profile") or {})
    except Exception:
        await _send(
            ws, {"type": "error", "code": "bad_profile", "message": "invalid profile"}
        )
        return None
    if not verify_profile(profile):
        await _send(
            ws,
            {
                "type": "error",
                "code": "auth",
                "message": "profile signature/fingerprint invalid",
            },
        )
        return None

    addresses = msg.get("addresses") or []
    _profiles[profile.node_id] = _Entry(profile, ws=ws, addresses=addresses)

    # Index for matchmaking. Indexing and querying both go through `get_embedding`, so
    # even the local fallback embedding stays self-consistent (see the design doc).
    init_db()
    text = _profile_text(profile)
    embedding, source = await get_embedding(text)
    upsert_document(
        profile.node_id,
        PROFILE_COLLECTION,
        text,
        {"node_id": profile.node_id, "_embedding_source": source},
        embedding,
    )
    save_profiles()
    await _send(ws, {"type": "published", "ok": True, "node_id": profile.node_id})
    return profile.node_id


async def _handle_search(ws: WebSocket, msg: dict[str, Any]) -> None:
    query = str(msg.get("query") or "").strip()
    limit = int(msg.get("limit") or 10)
    if not query:
        await _send(ws, {"type": "candidates", "results": []})
        return
    init_db()
    embedding, _ = await get_embedding(query)
    matches = search_documents(PROFILE_COLLECTION, embedding, max(limit, 1))
    results: list[dict[str, Any]] = []
    for match in matches:
        entry = _profiles.get(match["id"])
        if entry is None or entry.profile.visibility != "public":
            continue
        results.append(
            CommonsCandidate(
                profile=entry.profile, score=float(match["score"])
            ).model_dump()
        )
    await _send(ws, {"type": "candidates", "results": results[:limit]})


# ---- consent handshake ------------------------------------------------------------


def _reachability(node_id: str) -> dict[str, Any]:
    """How a peer dials this node — the candidates captured at publish time."""
    entry = _profiles.get(node_id)
    if entry is None:
        return {"node_id": node_id}
    return {
        "node_id": node_id,
        "public_key": entry.profile.public_key,
        "node_name": entry.profile.display_name,
        "addresses": entry.addresses,
    }


async def _handle_connect_request(requester_id: str, msg: dict[str, Any]) -> None:
    """A wants to meet B: forward the request to B (online only). The index never
    hands out reachability here — that waits for B's explicit consent."""
    target_id = str(msg.get("to_node_id") or "")
    target = _profiles.get(target_id)
    requester = _profiles.get(requester_id)
    if target is None or target.ws is None:
        if requester is not None and requester.ws is not None:
            await _send(
                requester.ws,
                {
                    "type": "request_failed",
                    "to_node_id": target_id,
                    "reason": "offline",
                },
            )
        return
    request_id = uuid.uuid4().hex[:12]
    _pending[request_id] = (requester_id, target_id)
    from_profile = (
        requester.profile.model_dump()
        if requester is not None
        else {"node_id": requester_id}
    )
    await _send(
        target.ws,
        {
            "type": "connect_request",
            "request_id": request_id,
            "from": from_profile,
            "note": str(msg.get("note") or ""),
        },
    )


async def _handle_connect_response(responder_id: str, msg: dict[str, Any]) -> None:
    """B accepts or declines. On mutual consent the index tells both sides to link up —
    the requester dials, the responder waits to accept (mirrors the lobby handoff)."""
    request_id = str(msg.get("request_id") or "")
    pending = _pending.pop(request_id, None)
    if pending is None:
        return
    requester_id, target_id = pending
    if responder_id != target_id:
        return  # only the node that was asked may answer
    requester = _profiles.get(requester_id)
    if not bool(msg.get("accept")):
        if requester is not None and requester.ws is not None:
            await _send(
                requester.ws,
                {"type": "declined", "request_id": request_id, "node_id": target_id},
            )
        return
    if requester is not None and requester.ws is not None:
        await _send(
            requester.ws,
            {
                "type": "connected",
                "request_id": request_id,
                "dial": True,
                "peer": _reachability(target_id),
            },
        )
    responder = _profiles.get(target_id)
    if responder is not None and responder.ws is not None:
        await _send(
            responder.ws,
            {
                "type": "connected",
                "request_id": request_id,
                "dial": False,
                "peer": _reachability(requester_id),
            },
        )
