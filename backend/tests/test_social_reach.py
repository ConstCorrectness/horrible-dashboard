"""Reaching a friend wherever they are: dial order, and reconnecting on our own.

Direct connections are an optimization for the same network; the relay is how two
people at two homes reach each other. And a friend is redialed when their session
is gone, instead of staying offline until someone presses connect.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from backend.modules.social import roster, store


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A fresh data dir and app.db, with the identity caches cleared."""
    from backend.modules.network import identity as node_identity
    from backend.modules.social import identity as person_identity

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()
    store.init_social_db()
    yield tmp_path
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()


class DialHub:
    """Records every dial; `answers` maps an address to the node id that answers."""

    def __init__(self, answers: dict[str, str], *, relay: bool = True) -> None:
        self.answers = answers
        self.dials: list[tuple[str, str]] = []
        self.peers: dict[str, object] = {}
        self.strangers: dict[str, object] = {}
        self.sent: list[tuple[str, str]] = []
        self.transports = [SimpleNamespace(name="direct")] + (
            [SimpleNamespace(name="relay")] if relay else []
        )
        self.slow: set[str] = set()

    async def connect(
        self,
        address: str,
        transport: str = "direct",
        token=None,
        *,
        timeout: float = 20.0,
    ):
        self.dials.append((transport, address))
        if address in self.slow:
            await asyncio.sleep(timeout)
            raise TimeoutError(address)
        node = self.answers.get(address)
        if node is None:
            raise ConnectionError(f"nothing at {address}")
        self.strangers[node] = object()
        return SimpleNamespace(node_id=node)

    async def send_to(self, node_id: str, msg_type: str, data: dict[str, Any]) -> None:
        self.sent.append((node_id, msg_type))

    def set_trusted(self, node_id: str, trusted: bool) -> None:
        pass


@pytest.fixture()
def lookup(monkeypatch):
    records: dict[str, list[str]] = {}

    async def fake_lookup(person_id: str) -> list[str]:
        return list(records.get(person_id, []))

    monkeypatch.setattr(roster.directory, "lookup", fake_lookup)
    return records


def _hub(monkeypatch, hub: DialHub) -> DialHub:
    monkeypatch.setattr(roster, "peer_hub", hub)
    monkeypatch.setattr(roster, "DIRECT_DIAL_BUDGET_S", 0.2)
    monkeypatch.setattr(roster, "_reconnect_backoff", {})
    return hub


def test_the_relay_reaches_a_friend_no_address_can(data_dir, monkeypatch, lookup):
    """Their presence record lists LAN and tailnet addresses from *their* networks —
    none reachable from here — and a relay candidate, which is."""
    hub = _hub(
        monkeypatch,
        DialHub({"relay:theirnode": "theirnode"}),
    )
    hub.slow = {"ws://10.0.0.142:8100/peer-ws"}
    lookup["friend"] = [
        "ws://10.0.0.142:8100/peer-ws",
        "ws://192.168.1.9:8100/peer-ws",
        "relay:theirnode",
    ]

    started = time.monotonic()
    node = asyncio.run(roster._dial("friend", None))
    elapsed = time.monotonic() - started

    assert node == "theirnode"
    assert hub.dials[-1] == ("relay", "relay:theirnode")
    # Direct got its short budget, not a full handshake timeout per address.
    assert elapsed < 2.0


def test_a_direct_address_that_answers_wins_and_the_relay_is_not_used(
    data_dir,
    monkeypatch,
    lookup,
):
    hub = _hub(
        monkeypatch,
        DialHub(
            {"ws://10.0.0.18:8100/peer-ws": "lan-node", "relay:lan-node": "lan-node"}
        ),
    )
    lookup["friend"] = ["ws://10.0.0.18:8100/peer-ws", "relay:lan-node"]
    assert asyncio.run(roster._dial("friend", None)) == "lan-node"
    assert all(t == "direct" for t, _ in hub.dials)


def test_the_address_you_typed_is_tried_first(data_dir, monkeypatch, lookup):
    hub = _hub(monkeypatch, DialHub({"ws://100.91.147.73:8100/peer-ws": "pi"}))
    assert (
        asyncio.run(roster._dial("friend", "ws://100.91.147.73:8100/peer-ws")) == "pi"
    )
    assert hub.dials[0] == ("direct", "ws://100.91.147.73:8100/peer-ws")


def test_a_pending_request_is_resent_when_the_friend_is_reached(
    data_dir,
    monkeypatch,
    lookup,
):
    hub = _hub(monkeypatch, DialHub({"relay:their-node": "their-node"}))
    store.upsert_friend("pendingperson0000", display_name="Ben", status="pending_out")
    lookup["pendingperson0000"] = ["relay:their-node"]

    connected = asyncio.run(roster.reconnect_round())

    assert connected == ["pendingperson0000"]
    assert (("their-node", roster.SOCIAL_FRIEND_REQUEST)) in hub.sent


def test_an_offline_friend_backs_off_instead_of_being_dialed_every_minute(
    data_dir,
    monkeypatch,
    lookup,
):
    hub = _hub(monkeypatch, DialHub({}))
    store.upsert_friend("offlineperson0000", display_name="Ada", status="accepted")
    lookup["offlineperson0000"] = ["relay:gone"]

    now = 1000.0
    asyncio.run(roster.reconnect_round(now=now))
    first = len(hub.dials)
    asyncio.run(roster.reconnect_round(now=now + 30))  # inside the first backoff
    assert len(hub.dials) == first
    asyncio.run(roster.reconnect_round(now=now + 61))  # backoff elapsed
    assert len(hub.dials) > first
    next_at, backoff = roster._reconnect_backoff["offlineperson0000"]
    assert backoff == 120.0 and next_at == now + 61 + 120.0


def test_a_friend_already_connected_is_not_redialed(data_dir, monkeypatch, lookup):
    hub = _hub(monkeypatch, DialHub({"relay:n": "n"}))
    store.upsert_friend("onlineperson00000", display_name="Cy", status="accepted")
    store.upsert_device("n", "onlineperson00000", "k", "box")
    hub.peers["n"] = object()
    lookup["onlineperson00000"] = ["relay:n"]
    assert asyncio.run(roster.reconnect_round()) == []
    assert hub.dials == []


def test_our_presence_record_advertises_the_relay(data_dir, monkeypatch):
    from backend.modules.network import hub as hub_module
    from backend.modules.network import identity as node_identity
    from backend.modules.social import directory

    monkeypatch.setattr(
        hub_module.peer_hub, "transports", [SimpleNamespace(name="relay")]
    )
    me = node_identity.load_identity().node_id
    assert directory.relay_candidates() == [f"relay:{me}"]
    monkeypatch.setattr(hub_module.peer_hub, "transports", [])
    assert directory.relay_candidates() == []


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("relay:theirnode000000", "relay:theirnode000000"),
        ("ws://10.0.0.142:8100/peer-ws", "ws://10.0.0.142:8100/peer-ws"),
        ("10.0.0.142:53122", None),  # an inbound socket's ephemeral port
        (None, None),
    ],
)
def test_only_an_address_that_can_be_dialed_back_is_remembered(address, expected):
    session = SimpleNamespace(info=SimpleNamespace(address=address))
    assert roster.dialable_address(session) == expected


def test_a_friends_hello_over_the_relay_records_the_relay_address(
    data_dir,
    monkeypatch,
):
    """So the reconnect after a restart works with the presence directory down."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from backend.modules.network.models import PeerEnvelope
    from backend.modules.social import identity as person_identity

    monkeypatch.setattr(roster, "peer_hub", DialHub({}))
    friend = person_identity.PersonIdentity(Ed25519PrivateKey.generate())
    store.upsert_friend(friend.person_id, display_name="Ben", status="accepted")
    cert = friend.issue_device_cert("bennode000000000", "pub", "laptop")
    session = SimpleNamespace(info=SimpleNamespace(address="relay:bennode000000000"))

    asyncio.run(
        roster.handle_hello(
            roster.peer_hub,
            session,
            PeerEnvelope(
                type=roster.SOCIAL_HELLO, src="bennode000000000", data={"cert": cert}
            ),
        )
    )
    devices = store.list_devices(friend.person_id)
    assert devices[0]["last_address"] == "relay:bennode000000000"


def test_a_username_alone_reaches_the_machine_behind_it(data_dir, monkeypatch, lookup):
    """No Atlas, no address: the game server's directory entry lists Ben's machine,
    and the dial goes to it over the relay."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from backend.modules.network import identity as node_identity
    from backend.modules.social import handles
    from backend.modules.social import identity as person_identity

    ben = person_identity.PersonIdentity(Ed25519PrivateKey.generate())
    ben_node = node_identity.Identity(Ed25519PrivateKey.generate())
    entry = {
        "handle": "ben",
        "display_name": "Ben",
        "person_id": ben.person_id,
        "person_public_key": ben.public_key,
        "devices": [
            {
                "node_id": ben_node.node_id,
                "cert": ben.issue_device_cert(
                    ben_node.node_id, ben_node.public_key, ""
                ),
            }
        ],
    }

    async def fake_resolve(handle: str):
        return entry if handle == "@ben" else None

    monkeypatch.setattr(handles, "resolve", fake_resolve)
    hub = _hub(monkeypatch, DialHub({f"relay:{ben_node.node_id}": ben_node.node_id}))

    async def go():
        person_id, error = await roster.resolve_target("@ben")
        assert error is None and person_id == ben.person_id
        return await roster._dial(person_id, None)

    assert asyncio.run(go()) == ben_node.node_id
    assert hub.dials == [("relay", f"relay:{ben_node.node_id}")]
