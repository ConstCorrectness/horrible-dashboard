"""Compute leases: lending a peer this node's GPU, model, or installed extras.

A lease is a revocable, expiring grant of one **service** to one peer. It is
deliberately service-generic (`"llama"`, `"embed"`, `"stt"`, `"trace"`) rather
than llama-specific, because everything that borrows -- a model, an embedding
batch, an eval sweep, a Whisper install -- wants the same lifecycle and the same
consent.

**Consent is three gates, not two**, and they are independent on purpose:

1. `network.allowComputeLending` -- default **false**. Nothing is lent until
   somebody says so, mirroring `network.allowRemoteAgent` exactly.
2. `network.computeLeasePolicy` -- `ask` | `trusted` | `off`, default **`ask`**,
   and it **fails closed** on any unrecognised value, the way
   `agent_bridge._remote_mode()` falls back to read-only `plan`. A typo in a
   setting must never widen access.
3. `session.info.trusted` -- unconditionally, like every actuating handler here.
   Friendship grants reachability, not authority; accepting a friend is not
   agreeing to run their workloads.

**The default grants only against the model already loaded.** Evicting somebody's
own chat model because a friend asked should not be reachable by default, and a
refusal naming what *is* loaded ("I'm serving gemma, ask for that") is a better
experience than a silent swap. This is what makes the `serving` attr in the
`inference` capability load-bearing: a borrower reads it and asks for the hot
model, and the eviction problem disappears for the common case.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.modules.network.tunnel import tunnels

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

COMPUTE_REQUEST = "compute_request"
COMPUTE_GRANT = "compute_grant"
COMPUTE_DENY = "compute_deny"
COMPUTE_RENEW = "compute_renew"
COMPUTE_REVOKE = "compute_revoke"

#: Default lease length. Short enough that a forgotten lease expires on its own,
#: long enough that a chat turn or an embedding batch does not race it.
DEFAULT_DURATION_S = 900.0
MAX_DURATION_S = 3600.0

#: How often expired leases are swept.
SWEEP_INTERVAL_S = 30.0


@dataclass
class Lease:
    """One grant, from the lender's point of view."""

    lease_id: str
    holder: str
    service: str
    model: str | None
    granted_at: float
    expires_at: float
    bytes_used: int = 0

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaseId": self.lease_id,
            "holder": self.holder,
            "service": self.service,
            "model": self.model,
            "grantedAt": self.granted_at,
            "expiresAt": self.expires_at,
            "bytesUsed": self.bytes_used,
        }


@dataclass
class Borrowed:
    """One lease this node holds on somebody else's machine."""

    lease_id: str
    node_id: str
    service: str
    model: str | None
    expires_at: float
    endpoint: str = ""
    #: Set when the lender revokes mid-use, so the failure can be reported as what
    #: it was rather than as a generic connection error.
    revoked_reason: str | None = None
    tunnel: Any = None

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


def _setting(key: str, default: Any) -> Any:
    from backend.modules.settings.routes import get_value

    return get_value(key, default)


def lending_enabled() -> bool:
    return bool(_setting("network.allowComputeLending", False))


def lease_policy() -> str:
    """`ask` | `trusted` | `off`, failing closed on anything unrecognised.

    The same reasoning as `network.remoteAgentMode`: an unknown value is a typo or
    a downgrade from a newer version, and either way the safe reading is the
    narrowest one. Widening access because a string did not parse is how a setting
    becomes a vulnerability.
    """
    raw = str(_setting("network.computeLeasePolicy", "ask") or "ask")
    return raw if raw in ("ask", "trusted", "off") else "off"


class LeaseManager:
    def __init__(self) -> None:
        #: Leases granted to others (we are the lender).
        self.granted: dict[str, Lease] = {}
        #: Leases we hold on others (we are the borrower).
        self.borrowed: dict[str, Borrowed] = {}
        self._sweeper: asyncio.Task[None] | None = None
        self._listeners: list[Any] = []

    # ---- lender ----------------------------------------------------------------

    def authorize(self, node_id: str, service: str, lease_id: str) -> tuple[bool, str]:
        """The check `tunnel.TunnelManager` runs on every `STREAM_OPEN`.

        Re-checked per connection rather than once at grant time: a lease can be
        revoked or expire between opening two sockets, and a borrower holding an
        old lease id must not keep getting service.
        """
        lease = self.granted.get(lease_id)
        if lease is None:
            return False, "no such lease"
        if lease.holder != node_id:
            return False, "that lease belongs to another node"
        if lease.expired:
            self.granted.pop(lease_id, None)
            return False, "lease expired"
        if lease.service != service:
            return False, f"lease is for {lease.service!r}, not {service!r}"
        return True, ""

    def _may_grant(self, session: PeerSession, model: str | None) -> tuple[bool, str]:
        if not session.info.trusted:
            return False, "not a trusted peer"
        if not lending_enabled():
            return False, "compute lending is disabled on this node"
        policy = lease_policy()
        if policy == "off":
            return False, "compute lending is disabled on this node"
        if policy == "ask":
            # Not yet wired to a prompt. Denying is the honest behaviour until it
            # is: silently treating "ask" as "yes" would grant access the user
            # believes they still have to approve.
            return False, "this node asks before lending; no approval UI yet"
        return self._model_ok(model)

    def _model_ok(self, model: str | None) -> tuple[bool, str]:
        """Only lend what is already loaded, unless told otherwise.

        Evicting the user's own chat model because a friend asked is not something
        that should happen without being chosen, and the refusal names what *is*
        loaded so the borrower can simply ask for that instead.
        """
        from backend.modules.llamacpp.server import llama_manager

        if model is None:
            return True, ""
        if not llama_manager.running():
            return False, "no model is loaded here"
        if llama_manager.alias == model:
            return True, ""
        if bool(_setting("network.computeAllowModelSwap", False)):
            return True, ""
        return False, f"this node is serving {llama_manager.alias!r}; ask for that"

    def grant(
        self, node_id: str, service: str, model: str | None, duration_s: float
    ) -> Lease:
        now = time.time()
        lease = Lease(
            lease_id=uuid.uuid4().hex,
            holder=node_id,
            service=service,
            model=model,
            granted_at=now,
            expires_at=now + min(max(duration_s, 1.0), MAX_DURATION_S),
        )
        self.granted[lease.lease_id] = lease
        self._notify()
        return lease

    async def handle_request(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        data = env.data or {}
        service = str(data.get("service") or "")
        model = data.get("model")
        model = str(model) if model else None
        try:
            duration = float(data.get("duration_s") or DEFAULT_DURATION_S)
        except (TypeError, ValueError):
            duration = DEFAULT_DURATION_S

        ok, reason = self._may_grant(session, model)
        if not ok or not service:
            await hub.send_to(
                session.info.node_id,
                COMPUTE_DENY,
                {"reason": reason or "malformed request"},
                re=env.msg_id,
            )
            return

        lease = self.grant(session.info.node_id, service, model, duration)
        logger.info(
            "granted compute lease %s (%s) to %s",
            lease.lease_id,
            service,
            session.info.node_id,
        )
        await hub.send_to(
            session.info.node_id, COMPUTE_GRANT, lease.to_dict(), re=env.msg_id
        )

    async def handle_renew(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        data = env.data or {}
        lease = self.granted.get(str(data.get("lease_id") or ""))
        if lease is None or lease.holder != session.info.node_id or lease.expired:
            await hub.send_to(
                session.info.node_id,
                COMPUTE_DENY,
                {"reason": "no such lease"},
                re=env.msg_id,
            )
            return
        try:
            extra = float(data.get("duration_s") or DEFAULT_DURATION_S)
        except (TypeError, ValueError):
            extra = DEFAULT_DURATION_S
        lease.expires_at = time.time() + min(max(extra, 1.0), MAX_DURATION_S)
        self._notify()
        await hub.send_to(
            session.info.node_id, COMPUTE_GRANT, lease.to_dict(), re=env.msg_id
        )

    async def handle_revoke(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        """Either side may revoke: the borrower releasing politely, the lender
        reclaiming its machine."""
        data = env.data or {}
        lease_id = str(data.get("lease_id") or "")
        reason = str(data.get("reason") or "revoked by the other side")

        lease = self.granted.pop(lease_id, None)
        if lease is not None and lease.holder == session.info.node_id:
            tunnels.close_lease_streams(lease_id, session.info.node_id)
            self._notify()
            return

        borrowed = self.borrowed.get(lease_id)
        if borrowed is not None and borrowed.node_id == session.info.node_id:
            borrowed.revoked_reason = reason
            await self.release_borrowed(lease_id, notify_peer=False)

    async def revoke(
        self, hub: PeerHub, lease_id: str, reason: str = "revoked"
    ) -> bool:
        """Reclaim a lease we granted, closing its streams immediately."""
        lease = self.granted.pop(lease_id, None)
        if lease is None:
            return False
        tunnels.close_lease_streams(lease_id, lease.holder)
        self._notify()
        try:
            await hub.send_to(
                lease.holder,
                COMPUTE_REVOKE,
                {"lease_id": lease_id, "reason": reason},
            )
        except Exception:  # noqa: BLE001 - the peer may already be gone
            pass
        return True

    # ---- borrower --------------------------------------------------------------

    async def request(
        self,
        hub: PeerHub,
        node_id: str,
        service: str,
        *,
        model: str | None = None,
        duration_s: float = DEFAULT_DURATION_S,
    ) -> Borrowed:
        """Ask a peer for a lease and open the tunnel that makes it usable."""
        reply = await hub.request(
            node_id,
            COMPUTE_REQUEST,
            {"service": service, "model": model, "duration_s": duration_s},
            timeout=30.0,
        )
        data = reply.data or {}
        if reply.type == COMPUTE_DENY or not data.get("leaseId"):
            raise PermissionError(str(data.get("reason") or "lease denied"))

        borrowed = Borrowed(
            lease_id=str(data["leaseId"]),
            node_id=node_id,
            service=service,
            model=data.get("model"),
            expires_at=float(data.get("expiresAt") or 0.0),
        )
        tunnel = await tunnels.open_tunnel(hub, node_id, service, borrowed.lease_id)
        borrowed.tunnel = tunnel
        borrowed.endpoint = tunnel.endpoint
        self.borrowed[borrowed.lease_id] = borrowed
        self._notify()
        return borrowed

    async def renew_borrowed(
        self, hub: PeerHub, lease_id: str, *, duration_s: float = DEFAULT_DURATION_S
    ) -> Borrowed:
        """Extend a lease we hold, keeping its tunnel open.

        The lender is the authority on the new expiry -- it clamps to
        `MAX_DURATION_S` and may refuse outright -- so the answer is read back
        rather than assumed. Believing our own requested duration is how a
        borrower keeps sending work down a tunnel the lender has already closed.
        """
        borrowed = self.borrowed.get(lease_id)
        if borrowed is None:
            raise KeyError(f"no lease {lease_id} held here")
        reply = await hub.request(
            borrowed.node_id,
            COMPUTE_RENEW,
            {"lease_id": lease_id, "duration_s": duration_s},
            timeout=30.0,
        )
        data = reply.data or {}
        if reply.type == COMPUTE_DENY or not data.get("expiresAt"):
            raise PermissionError(str(data.get("reason") or "renewal denied"))
        borrowed.expires_at = float(data["expiresAt"])
        self._notify()
        return borrowed

    async def release_borrowed(
        self, lease_id: str, *, notify_peer: bool = True
    ) -> None:
        borrowed = self.borrowed.pop(lease_id, None)
        if borrowed is None:
            return
        if borrowed.tunnel is not None:
            await borrowed.tunnel.close()
        tunnels.close_lease_streams(lease_id, borrowed.node_id)
        self._notify()
        if not notify_peer:
            return
        from backend.modules.network.hub import peer_hub

        try:
            await peer_hub.send_to(
                borrowed.node_id,
                COMPUTE_REVOKE,
                {"lease_id": lease_id, "reason": "released"},
            )
        except Exception:  # noqa: BLE001
            pass

    async def end(self, hub: PeerHub, lease_id: str) -> dict[str, Any]:
        """End a lease in whichever direction it runs.

        One entry point because "stop this" is one intent, and which side of a
        lease this node is on is a fact the code can look up rather than something
        the caller -- an agent tool, a Revoke button -- should have to know. Both
        callers report the same three answers.
        """
        borrowed = self.borrowed.get(lease_id)
        if borrowed is not None:
            node_id = borrowed.node_id
            await self.release_borrowed(lease_id)
            return {"ok": True, "released": "borrowed", "node": node_id}
        lease = self.granted.get(lease_id)
        if lease is not None:
            await self.revoke(hub, lease_id, reason="revoked by the lender")
            return {"ok": True, "released": "granted", "node": lease.holder}
        return {"ok": False, "reason": f"no lease {lease_id!r} in either direction"}

    def active_borrow(self, service: str = "llama") -> Borrowed | None:
        """The live lease for `service`, if any.

        Read by `agent/routes._endpoint_for`, which is why it must not raise and
        must not consider an expired lease live -- a stale endpoint would send a
        chat turn to a closed port.
        """
        for borrowed in self.borrowed.values():
            if borrowed.service == service and not borrowed.expired:
                return borrowed
        return None

    # ---- lifecycle -------------------------------------------------------------

    def subscribe(self, cb: Any) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self.snapshot())
            except Exception:
                logger.exception("lease listener failed")

    def snapshot(self) -> dict[str, Any]:
        return {
            "granted": [lease.to_dict() for lease in self.granted.values()],
            "borrowed": [
                {
                    "leaseId": b.lease_id,
                    "nodeId": b.node_id,
                    "service": b.service,
                    "model": b.model,
                    "expiresAt": b.expires_at,
                    "endpoint": b.endpoint,
                }
                for b in self.borrowed.values()
            ],
        }

    async def sweep_once(self, hub: PeerHub) -> int:
        expired = [lid for lid, lease in self.granted.items() if lease.expired]
        for lease_id in expired:
            await self.revoke(hub, lease_id, reason="expired")
        for lease_id in [lid for lid, b in self.borrowed.items() if b.expired]:
            await self.release_borrowed(lease_id)
        return len(expired)

    async def _sweep_loop(self, hub: PeerHub) -> None:
        while True:
            try:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                await self.sweep_once(hub)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("lease sweep failed")

    def start(self, hub: PeerHub) -> None:
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop(hub))

    def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None


leases = LeaseManager()


def _llama_endpoint() -> tuple[str, int] | None:
    """Where this node's `llama-server` actually is.

    Resolved per connection, never cached: the port is chosen at spawn and falls
    back to an ephemeral one when 8080 is occupied, which is exactly the case a
    remembered port gets wrong.
    """
    from urllib.parse import urlparse

    from backend.modules.llamacpp.server import llama_manager

    if not llama_manager.running():
        return None
    parsed = urlparse(llama_manager.endpoint)
    if not parsed.hostname or not parsed.port:
        return None
    return parsed.hostname, parsed.port


def _own_api_endpoint() -> tuple[str, int] | None:
    """This node's own FastAPI, for extras that are exposed as HTTP routes.

    `voice` (`/api/agent/stt`, `/api/agent/tts`) and `browser` are already routes,
    so lending them is a matter of letting a lease holder reach this backend --
    through the tunnel only, never by binding another port.
    """
    import os

    try:
        port = int(os.environ.get("HORRIBLE_DEV_BACKEND_PORT", "8000"))
    except ValueError:
        port = 8000
    return "127.0.0.1", port


def register(hub: PeerHub) -> None:
    from backend.modules.network import borrow, tunnel

    tunnel.register(hub)
    tunnels.set_authorizer(leases.authorize)
    tunnels.register_service("llama", _llama_endpoint)
    # Extras are HTTP routes on this backend rather than separate processes, so
    # they all resolve to the same place; the lease `service` is what the
    # authorizer checks, so one of them does not grant the others.
    for service in ("voice", "clip", "trace", "browser", "embed"):
        tunnels.register_service(service, _own_api_endpoint)
    borrow.register()

    # All `detach`: each awaits real work (a settings read, a spawn check, a
    # tunnel teardown) and `handle_revoke` awaits a send, so inline would put it
    # on the pump the reply travels over.
    hub.register_handler(COMPUTE_REQUEST, leases.handle_request, mode="detach")
    hub.register_handler(COMPUTE_RENEW, leases.handle_renew, mode="detach")
    hub.register_handler(COMPUTE_REVOKE, leases.handle_revoke, mode="detach")

    # Push every lease change to the browser on the `network` channel. Pushed
    # rather than polled because the interesting transitions -- a peer revoking
    # mid-turn, a lease expiring -- originate on the *other* node, so a UI that
    # only refreshed on its own actions would show a lease that has already been
    # taken away.
    leases.subscribe(lambda snap: hub.emit("lease_update", snap))
    leases.start(hub)
