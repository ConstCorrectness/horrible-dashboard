"""Node-side **commons client**: connect to a commons index, publish this node's
signed profile, and browse/search other nodes' profiles.

One `CommonsClient` per node (a process-global singleton, like `lobby_client`). It owns
the outbound WebSocket to `commons.serverUrl`, builds + signs this node's
`CommonsProfile` from settings, keeps the directory/search snapshot the frontend
renders (fanned out over the `/ws` `commons` channel), and exposes search.

This is **Phase 2** of docs/architecture/agent-commons.mdx (browse/search end to end).
The consent handshake (request-to-meet → peer link) is Phase 3 and not here yet.

See docs/modules/commons.mdx and backend/modules/network/commons_server.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from backend.modules.network import ice, identity, trust
from backend.modules.network.hub import peer_hub
from backend.modules.network.models import (
    CommonsProfile,
    canonical_profile_bytes,
    canonical_vouch_bytes,
)
from backend.modules.settings.routes import get_value, set_value

logger = logging.getLogger(__name__)


def _build_profile() -> CommonsProfile:
    """This node's storefront, built from settings and signed with the node key.

    Signing happens here (backend) because the private key never crosses an API; the
    frontend can edit the *settings* that feed this, but cannot sign."""
    signer = peer_hub.signer
    tags = [
        t.strip()
        for t in str(get_value("commons.tags", "") or "").split(",")
        if t.strip()
    ]
    visibility = str(get_value("commons.visibility", "public") or "public")
    profile = CommonsProfile(
        node_id=signer.node_id,
        public_key=signer.public_key,
        display_name=identity.node_name(),
        headline=str(get_value("commons.headline", "") or ""),
        bio=str(get_value("commons.bio", "") or "") or None,
        tags=tags,
        seeking=str(get_value("commons.seeking", "") or "") or None,
        agent_capabilities=peer_hub.capabilities(),
        visibility=visibility if visibility in ("public", "unlisted") else "public",  # type: ignore[arg-type]
    )
    profile.sig = signer.sign(canonical_profile_bytes(profile))
    return profile


class CommonsClient:
    def __init__(self) -> None:
        self.url: str | None = None
        self.connected = False
        self.directory: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.requests: list[
            dict[str, Any]
        ] = []  # inbound meet requests awaiting consent
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._subscribers: set[Any] = set()

    # ---- frontend fanout ----------------------------------------------------------

    def subscribe(self, cb: Any) -> Any:
        self._subscribers.add(cb)
        return lambda: self._subscribers.discard(cb)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        for cb in list(self._subscribers):
            try:
                cb(event, data)
            except Exception:
                logger.exception("commons subscriber failed")

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "url": self.url,
            "self": peer_hub.identity().model_dump(),
            "my_profile": _build_profile().model_dump(),
            "directory": self.directory,
            "results": self.results,
            "requests": self.requests,
        }

    # ---- lifecycle ----------------------------------------------------------------

    async def start(self) -> None:
        if not bool(get_value("commons.enabled", False)):
            return
        url = str(get_value("commons.serverUrl", "") or "").strip()
        if url:
            await self.connect(url)

    async def connect(self, url: str) -> None:
        await self.disconnect()
        self.url = url
        try:
            self._ws = await ws_connect(url)
        except Exception as exc:
            logger.info("commons connect failed: %s", exc)
            self._emit("error", {"message": f"commons connect failed: {exc}"})
            return
        self.connected = True
        self._reader = asyncio.ensure_future(self._read_loop())
        self._emit("state", self.snapshot())
        if bool(get_value("commons.autoPublish", True)):
            await self.publish()
        await self.request_directory()

    async def disconnect(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self.connected = False

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(message))
        except ConnectionClosed:
            self.connected = False

    # ---- operations (driven by the browser) ---------------------------------------

    async def publish(self) -> None:
        try:
            addresses = await ice.gather_candidates()
        except Exception:
            addresses = [trust.advertised_address()]
        profile = _build_profile()
        await self._send(
            {
                "type": "publish_profile",
                "profile": profile.model_dump(),
                "addresses": addresses,
            }
        )

    async def set_profile(self, fields: dict[str, Any]) -> None:
        """Persist this node's profile fields (the storefront) and republish. Signing
        stays here; the browser only supplies the editable text."""
        for key in ("headline", "bio", "tags", "seeking", "visibility"):
            if key in fields:
                set_value(f"commons.{key}", fields[key])
        if self.connected:
            await self.publish()
        self._emit("state", self.snapshot())

    async def search(self, query: str, limit: int = 10) -> None:
        await self._send({"type": "search", "query": query, "limit": limit})

    async def request_directory(self) -> None:
        await self._send({"type": "directory"})

    # ---- consent handshake --------------------------------------------------------

    async def request_connect(self, to_node_id: str, note: str = "") -> None:
        """Ask another node's human to meet — gated by their explicit consent."""
        await self._send(
            {"type": "connect_request", "to_node_id": to_node_id, "note": note}
        )

    async def respond(self, request_id: str, accept: bool) -> None:
        """Accept or decline an inbound meet request."""
        self.requests = [r for r in self.requests if r.get("request_id") != request_id]
        await self._send(
            {"type": "connect_response", "request_id": request_id, "accept": accept}
        )
        self._emit("requests", {"requests": self.requests})

    async def _dial(self, peer: dict[str, Any]) -> None:
        """Establish the peer link on mutual consent: direct → webrtc → relay (the same
        ladder the lobby uses). Runs detached so the read loop never blocks on it."""
        node_id = peer.get("node_id")
        if not node_id or node_id in peer_hub.peers:
            return
        for address in peer.get("addresses") or []:
            try:
                await peer_hub.connect(address, "direct")
                return
            except Exception as exc:
                logger.info("commons direct dial %s failed: %s", address, exc)
        if any(t.name == "webrtc" for t in peer_hub.transports):
            try:
                await peer_hub.connect(node_id, "webrtc")
                return
            except Exception as exc:
                logger.info("commons webrtc dial %s failed: %s", node_id, exc)
        try:
            await peer_hub.connect(node_id, "relay")
        except Exception as exc:
            logger.info("commons relay dial %s failed: %s", node_id, exc)
            self._emit("error", {"message": f"could not reach {node_id}"})

    # ---- reputation (trust tiers + blocklist + vouches) ---------------------------

    def _trusted_set(self) -> set[str]:
        """Node ids this viewer already trusts (paired, not blocked) — the basis for
        weighting vouches by *your* graph rather than a gameable global count."""
        return {
            nid
            for nid, rec in trust.load_known_peers().items()
            if rec.get("trusted") and not rec.get("blocked")
        }

    def _tier(self, node_id: str, vouchers: list[str] | None = None) -> str:
        """This viewer's trust tier for a node: `blocked`, `known` (already paired),
        `vouched` (vouched for by someone you trust), or `unknown`. Computed node-side
        against the local trust store — never trusted to the index — so it's
        viewer-relative."""
        if trust.is_blocked(node_id):
            return "blocked"
        if trust.is_trusted(node_id):
            return "known"
        if vouchers and self._trusted_set().intersection(vouchers):
            return "vouched"
        return "unknown"

    def _annotate(self, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                **p,
                "trust_tier": self._tier(
                    str(p.get("node_id") or ""), p.get("vouchers")
                ),
            }
            for p in profiles
        ]

    async def block(self, node_id: str) -> None:
        """Block a node: its meet requests are auto-declined and the peer fabric's
        admission check (`trust.evaluate`) also refuses it — one block ends contact.
        Survives re-discovery because the node id is a stable key fingerprint."""
        trust.save_known_peer(node_id, {"blocked": True, "trusted": False})
        self._reannotate()
        self._emit("state", self.snapshot())

    async def unblock(self, node_id: str) -> None:
        trust.save_known_peer(node_id, {"blocked": False})
        self._reannotate()
        self._emit("state", self.snapshot())

    async def vouch(self, subject_node_id: str) -> None:
        """Publish a signed attestation that you trust a node — raises it to the
        `vouched` tier for others who already trust *you*."""
        if not subject_node_id:
            return
        signer = peer_hub.signer
        sig = signer.sign(canonical_vouch_bytes(signer.node_id, subject_node_id))
        await self._send(
            {"type": "vouch", "subject_node_id": subject_node_id, "sig": sig}
        )
        await self.request_directory()

    async def report(self, subject_node_id: str, reason: str = "") -> None:
        """Send a moderation report to the index (recorded, not auto-acted)."""
        if not subject_node_id:
            return
        await self._send(
            {"type": "report", "subject_node_id": subject_node_id, "reason": reason}
        )

    def _reannotate(self) -> None:
        """Recompute trust tiers in place after a block/unblock changes the store."""
        for profile in self.directory:
            profile["trust_tier"] = self._tier(
                str(profile.get("node_id") or ""), profile.get("vouchers")
            )
        for result in self.results:
            inner = result.get("profile") or {}
            inner["trust_tier"] = self._tier(
                str(inner.get("node_id") or ""), inner.get("vouchers")
            )

    # ---- inbound ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                raw = await self._ws.recv()
                try:
                    msg = json.loads(raw if isinstance(raw, str) else raw.decode())
                except ValueError:
                    continue
                await self._dispatch(msg)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("commons read loop failed")
        finally:
            self.connected = False
            self._emit("state", self.snapshot())

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "directory":
            self.directory = self._annotate(msg.get("profiles") or [])
            self._emit("directory", {"profiles": self.directory})
        elif mtype == "candidates":
            results = msg.get("results") or []
            for result in results:
                inner = result.get("profile") or {}
                inner["trust_tier"] = self._tier(
                    str(inner.get("node_id") or ""), inner.get("vouchers")
                )
            self.results = results
            self._emit("candidates", {"results": self.results})
        elif mtype == "published":
            self._emit("state", self.snapshot())
        elif mtype == "connect_request":
            request = {
                "request_id": msg.get("request_id"),
                "from": msg.get("from") or {},
                "note": msg.get("note") or "",
            }
            from_id = str(request["from"].get("node_id") or "")
            if from_id and trust.is_blocked(from_id):
                # Blocked node — auto-decline silently, never surface it.
                asyncio.create_task(self.respond(str(request["request_id"]), False))
                return
            self.requests = [
                r for r in self.requests if r.get("request_id") != request["request_id"]
            ]
            self.requests.append(request)
            self._emit("requests", {"requests": self.requests})
        elif mtype == "connected":
            peer = msg.get("peer") or {}
            if bool(msg.get("dial")):
                asyncio.create_task(self._dial(peer))
            self._emit("met", {"peer": peer, "request_id": msg.get("request_id")})
        elif mtype == "declined":
            self._emit("declined", {"node_id": msg.get("node_id")})
        elif mtype == "request_failed":
            self._emit(
                "error",
                {
                    "message": f"could not reach {msg.get('to_node_id')}: "
                    f"{msg.get('reason')}"
                },
            )
        elif mtype == "error":
            self._emit("error", {"message": msg.get("message", "commons error")})


commons_client = CommonsClient()


# ---- /ws `commons` channel bridge -------------------------------------------------


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "commons", "event": event, "data": data}


def subscribe_commons_conn(conn: Any) -> Any:
    """Fan commons state/directory/search events out to one browser connection."""

    def cb(event: str, data: dict[str, Any]) -> None:
        asyncio.ensure_future(conn.send_json(_evt(event, data)))

    return commons_client.subscribe(cb)


async def handle_commons_message(conn: Any, msg: dict[str, Any]) -> None:
    """Route an inbound `commons`-channel message from the browser to the client."""
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "state":
        await conn.send_json(_evt("state", commons_client.snapshot()))
    elif event == "connect":
        url = str(data.get("url") or get_value("commons.serverUrl", "") or "").strip()
        if url:
            asyncio.create_task(commons_client.connect(url))
    elif event == "disconnect":
        asyncio.create_task(commons_client.disconnect())
    elif event == "search":
        await commons_client.search(
            str(data.get("query") or ""), int(data.get("limit") or 10)
        )
    elif event == "directory":
        await commons_client.request_directory()
    elif event == "publish":
        asyncio.create_task(commons_client.publish())
    elif event == "request":
        await commons_client.request_connect(
            str(data.get("nodeId") or ""), str(data.get("note") or "")
        )
    elif event == "respond":
        await commons_client.respond(
            str(data.get("requestId") or ""), bool(data.get("accept"))
        )
    elif event == "block":
        asyncio.create_task(commons_client.block(str(data.get("nodeId") or "")))
    elif event == "unblock":
        asyncio.create_task(commons_client.unblock(str(data.get("nodeId") or "")))
    elif event == "vouch":
        await commons_client.vouch(str(data.get("nodeId") or ""))
    elif event == "report":
        await commons_client.report(
            str(data.get("nodeId") or ""), str(data.get("reason") or "")
        )
    elif event == "set_profile":
        await commons_client.set_profile(data if isinstance(data, dict) else {})
