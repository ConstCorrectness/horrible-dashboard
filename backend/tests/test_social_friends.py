"""The social module: friend codes, device certificates, the roster store, and the
friendship state machine.

Distinct from `test_social.py`, which covers the *game server's* Plaza. This suite
points `HORRIBLE_DATA_DIR` at a tmp dir so it never touches a developer's real
person key or roster, and clears the identity caches, which are `lru_cache`d by
path and would otherwise leak between tests.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import pytest

from backend.modules.network.models import PeerEnvelope
from backend.modules.social import identity as person_identity
from backend.modules.social import roster, store
from backend.modules.social.friendcode import (
    resolve_person_id,
    format_friend_code,
    is_friend_code,
    parse_friend_code,
)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A fresh data dir + app.db, with every identity cache cleared."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    person_identity._cached_identity.cache_clear()
    from backend.modules.network import identity as node_identity

    node_identity._cached_identity.cache_clear()
    store.init_social_db()
    yield tmp_path
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()


def _random_person_id() -> str:
    return person_identity.fingerprint(base64.b64encode(os.urandom(32)).decode())


# ---- friend codes -----------------------------------------------------------------


def test_friend_code_round_trips():
    for _ in range(200):
        person_id = _random_person_id()
        assert parse_friend_code(format_friend_code(person_id)) == person_id


def test_friend_code_tolerates_how_people_actually_paste_it():
    code = format_friend_code(_random_person_id())
    expected = parse_friend_code(code)
    for variant in (
        code.lower(),
        code.replace("-", " "),
        code.replace("-", ""),
        f"  {code}  ",
        code.removeprefix("HD-"),
    ):
        assert parse_friend_code(variant) == expected


def test_friend_code_rejects_a_typo():
    code = format_friend_code(_random_person_id())
    # Flip one character of the id half; the checksum must catch it rather than
    # silently resolving to a different (valid-looking) person.
    body = code.replace("-", "")[2:]
    flipped = "A" if body[0] != "A" else "B"
    assert not is_friend_code("HD-" + flipped + body[1:])


def test_friend_code_folds_only_impossible_digits():
    """0/1/8/9 never appear in base32, so folding them onto letters is safe and
    must not corrupt an id that legitimately contains 2-7."""
    person_id = "".join(c for c in _random_person_id())
    code = format_friend_code(person_id)
    assert parse_friend_code(code.replace("O", "0").replace("I", "1")) == person_id


def test_resolve_accepts_a_bare_person_id_but_not_a_broken_code():
    person_id = _random_person_id()
    assert resolve_person_id(person_id) == person_id
    assert resolve_person_id(format_friend_code(person_id)) == person_id
    # A 20-character input is always judged as a code, so a bad checksum must
    # raise rather than quietly degrade into "treat it as a raw id".
    with pytest.raises(ValueError, match="typo"):
        resolve_person_id("HD-AAAA-BBBB-CCCC-DDDD-EEEE")
    with pytest.raises(ValueError):
        resolve_person_id("nonsense")


# ---- device certificates ----------------------------------------------------------


def test_device_cert_verifies(data_dir):
    me = person_identity.load_person()
    cert = me.issue_device_cert("node123", "nodepubkey", "desktop")
    assert person_identity.verify_device_cert(cert)


def test_tampered_device_cert_is_rejected(data_dir):
    me = person_identity.load_person()
    cert = me.issue_device_cert("node123", "nodepubkey", "desktop")
    forged = {**cert, "node_id": "someone-elses-node"}
    assert not person_identity.verify_device_cert(forged)


def test_cert_claiming_a_mismatched_person_id_is_rejected(data_dir):
    me = person_identity.load_person()
    cert = me.issue_device_cert("node123", "nodepubkey", "desktop")
    assert not person_identity.verify_device_cert({**cert, "person_id": "not-the-hash"})


def test_cert_for_another_node_is_not_accepted_off_the_wire(data_dir):
    """A valid certificate replayed by a machine it doesn't name must be refused —
    the check `verify_device_cert` leaves to its caller."""
    me = person_identity.load_person()
    cert = me.issue_device_cert("node-A", "pubkey-A", "laptop")
    env = PeerEnvelope(type=roster.SOCIAL_HELLO, src="node-B", data={"cert": cert})
    assert roster._accept_cert(env, cert, "Someone") is None
    assert store.person_for_node("node-B") is None


# ---- roster store -----------------------------------------------------------------


def test_roster_groups_devices_under_one_person(data_dir):
    person_id = _random_person_id()
    store.upsert_friend(person_id, display_name="Rob", status="accepted")
    store.upsert_device("node-desktop", person_id, "k1", "desktop")
    store.upsert_device("node-laptop", person_id, "k2", "laptop")

    friends = store.list_friends(online_nodes={"node-laptop"})
    assert len(friends) == 1, "two machines must be one friend, not two"
    friend = friends[0]
    assert {d.node_id for d in friend.devices} == {"node-desktop", "node-laptop"}
    assert friend.presence == "online", "one machine online makes the person online"


def test_person_is_offline_when_no_device_is_connected(data_dir):
    person_id = _random_person_id()
    store.upsert_friend(person_id, display_name="Rob", status="accepted")
    store.upsert_device("node-desktop", person_id, "k1", "desktop")
    assert store.list_friends(online_nodes=set())[0].presence == "offline"


def test_upsert_leaves_unspecified_fields_alone(data_dir):
    person_id = _random_person_id()
    store.upsert_friend(person_id, display_name="Rob", status="pending_out")
    store.set_status(person_id, "accepted")
    row = store.get_friend_row(person_id)
    assert row["display_name"] == "Rob" and row["status"] == "accepted"


def test_presence_update_does_not_erase_a_stored_address(data_dir):
    person_id = _random_person_id()
    store.upsert_device("node-a", person_id, "k", "box", address="ws://1.2.3.4/peer-ws")
    store.upsert_device("node-a", person_id, "k", "box")  # a bare presence touch
    assert store.list_devices(person_id)[0]["last_address"] == "ws://1.2.3.4/peer-ws"


# ---- the friendship state machine -------------------------------------------------


class FakeHub:
    """Enough of `PeerHub` for the roster: a connection table and a send log."""

    def __init__(self, connected: list[str] | None = None) -> None:
        self.peers = {n: object() for n in (connected or [])}
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.handlers: dict[str, Any] = {}

    def register_handler(self, msg_type: str, handler: Any) -> None:
        self.handlers[msg_type] = handler

    def subscribe(self, cb: Any) -> Any:
        return lambda: None

    async def send_to(self, node_id: str, msg_type: str, data: dict[str, Any]) -> None:
        if node_id not in self.peers:
            raise KeyError(node_id)
        self.sent.append((node_id, msg_type, data))


@pytest.fixture()
def hub(monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(roster, "peer_hub", fake)
    return fake


def _peer_cert(person_key: person_identity.PersonIdentity, node_id: str) -> dict:
    return person_key.issue_device_cert(node_id, f"pub-{node_id}", "their-box")


@pytest.fixture()
def stranger(tmp_path):
    """A second person, with their own key, standing in for the other node."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return person_identity.PersonIdentity(Ed25519PrivateKey.generate())


def test_inbound_request_lands_as_pending_and_grants_no_trust(data_dir, hub, stranger):
    async def go():
        cert = _peer_cert(stranger, "their-node")
        env = PeerEnvelope(
            type=roster.SOCIAL_FRIEND_REQUEST,
            src="their-node",
            data={"cert": cert, "display_name": "Stranger"},
        )
        await roster.handle_friend_request(hub, None, env)

        row = store.get_friend_row(stranger.person_id)
        assert row["status"] == "pending_in"
        from backend.modules.network import trust

        assert not trust.is_trusted("their-node"), (
            "a request alone must not grant trust"
        )

    asyncio.run(go())


def test_accepting_grants_fabric_trust_to_every_device(data_dir, hub, stranger):
    async def go():
        cert = _peer_cert(stranger, "their-node")
        await roster.handle_friend_request(
            hub,
            None,
            PeerEnvelope(
                type=roster.SOCIAL_FRIEND_REQUEST,
                src="their-node",
                data={"cert": cert, "display_name": "Stranger"},
            ),
        )
        store.upsert_device("their-laptop", stranger.person_id, "k2", "laptop")

        await roster.respond(stranger.person_id, accept=True)

        from backend.modules.network import trust

        assert store.get_friend_row(stranger.person_id)["status"] == "accepted"
        assert trust.is_trusted("their-node")
        assert trust.is_trusted("their-laptop"), (
            "every device of a friend becomes trusted"
        )

    asyncio.run(go())


def test_declining_removes_the_row(data_dir, hub, stranger):
    async def go():
        cert = _peer_cert(stranger, "their-node")
        await roster.handle_friend_request(
            hub,
            None,
            PeerEnvelope(
                type=roster.SOCIAL_FRIEND_REQUEST,
                src="their-node",
                data={"cert": cert, "display_name": "Stranger"},
            ),
        )
        await roster.respond(stranger.person_id, accept=False)
        assert store.get_friend_row(stranger.person_id) is None

    asyncio.run(go())


def test_crossing_requests_settle_as_accepted(data_dir, hub, stranger):
    async def go():
        """Both sides added each other before either answered — that is mutual consent,
        so it must not deadlock as two forever-pending rows."""
        store.upsert_friend(stranger.person_id, status="pending_out")
        hub.peers["their-node"] = object()
        cert = _peer_cert(stranger, "their-node")
        await roster.handle_friend_request(
            hub,
            None,
            PeerEnvelope(
                type=roster.SOCIAL_FRIEND_REQUEST,
                src="their-node",
                data={"cert": cert, "display_name": "Stranger"},
            ),
        )
        assert store.get_friend_row(stranger.person_id)["status"] == "accepted"
        assert any(m[1] == roster.SOCIAL_FRIEND_RESPONSE for m in hub.sent)

    asyncio.run(go())


def test_blocked_person_gets_silence(data_dir, hub, stranger):
    async def go():
        store.upsert_friend(stranger.person_id, status="blocked")
        cert = _peer_cert(stranger, "their-node")
        await roster.handle_friend_request(
            hub,
            None,
            PeerEnvelope(
                type=roster.SOCIAL_FRIEND_REQUEST,
                src="their-node",
                data={"cert": cert, "display_name": "Stranger"},
            ),
        )
        assert store.get_friend_row(stranger.person_id)["status"] == "blocked"
        assert hub.sent == [], "a blocked person must not learn they are blocked"

    asyncio.run(go())


def test_removing_a_friend_revokes_trust(data_dir, hub, stranger):
    async def go():
        store.upsert_friend(stranger.person_id, status="accepted")
        store.upsert_device("their-node", stranger.person_id, "k", "box")
        roster._grant_trust(stranger.person_id)
        from backend.modules.network import trust

        assert trust.is_trusted("their-node")

        await roster.remove(stranger.person_id)
        assert not trust.is_trusted("their-node")

    asyncio.run(go())


def test_cannot_friend_yourself(data_dir, hub):
    async def go():
        me = person_identity.load_person()
        friend, error = await roster.add_friend(me.friend_code)
        assert friend is None and "your own" in (error or "")

    asyncio.run(go())


def test_a_mistyped_code_fails_before_touching_the_network(data_dir, hub):
    async def go():
        friend, error = await roster.add_friend("HD-AAAA-BBBB-CCCC-DDDD-EEEE")
        assert friend is None and "typo" in (error or "")
        assert hub.sent == []

    asyncio.run(go())


class FakeSession:
    """A peer session carrying just the trust flag `handle_device_cert` consults."""

    def __init__(self, trusted: bool) -> None:
        self.info = type("Info", (), {"trusted": trusted})()


def test_an_untrusted_peer_cannot_talk_this_machine_out_of_its_identity(
    data_dir, hub, stranger
):
    async def go():
        from backend.modules.network import identity as node_identity

        me = node_identity.load_identity()
        hostile = stranger.issue_device_cert(me.node_id, me.public_key, "pwned")
        await roster.handle_device_cert(
            hub,
            FakeSession(trusted=False),
            PeerEnvelope(
                type=roster.SOCIAL_DEVICE_CERT, src="their-node", data={"cert": hostile}
            ),
        )
        assert not person_identity.is_linked_device()

    asyncio.run(go())


def test_a_trusted_peer_may_link_this_machine(data_dir, hub, stranger):
    """Consent is the invite this machine minted — redeeming it makes the peer
    trusted, and only then may it hand over a certificate.

    A second computer generates its own person key the moment anything asks who it
    is, so gating on "do I already hold a key" would refuse every real link.
    """

    async def go():
        from backend.modules.network import identity as node_identity

        person_identity.load_person()  # this machine has its own key already
        me = node_identity.load_identity()
        cert = stranger.issue_device_cert(me.node_id, me.public_key, "my-laptop")
        await roster.handle_device_cert(
            hub,
            FakeSession(trusted=True),
            PeerEnvelope(
                type=roster.SOCIAL_DEVICE_CERT, src="their-node", data={"cert": cert}
            ),
        )
        assert person_identity.is_linked_device()
        assert person_identity.effective_person_id() == stranger.person_id
        # And it now presents its owner's friend code, not its own unused one.
        assert roster.self_profile().person_id == stranger.person_id
        assert roster.self_profile().holds_person_key is False

    asyncio.run(go())


def test_a_cert_addressed_to_another_node_is_refused_even_from_a_trusted_peer(
    data_dir, hub, stranger
):
    async def go():
        cert = stranger.issue_device_cert("some-other-node", "k", "not-me")
        await roster.handle_device_cert(
            hub,
            FakeSession(trusted=True),
            PeerEnvelope(
                type=roster.SOCIAL_DEVICE_CERT, src="their-node", data={"cert": cert}
            ),
        )
        assert not person_identity.is_linked_device()

    asyncio.run(go())


# ---- the Atlas presence directory -------------------------------------------------


def test_presence_record_is_signed_and_verifies(data_dir):
    from backend.modules.social import directory

    record = directory.build_record()
    assert record is not None
    assert directory.verify_record(record)


def test_tampered_presence_record_is_rejected(data_dir):
    """The directory is untrusted infrastructure: it may withhold a record, but it
    must not be able to point us at someone else's address."""
    from backend.modules.social import directory

    record = directory.build_record()
    assert not directory.verify_record(
        {**record, "addresses": ["ws://attacker.example/peer-ws"]}
    )
    assert not directory.verify_record({**record, "person_id": "someone-else"})


def test_a_linked_machine_publishes_nothing(data_dir, hub, stranger):
    """Only the machine holding the person key can sign as that person."""
    from backend.modules.network import identity as node_identity
    from backend.modules.social import directory

    me = node_identity.load_identity()
    cert = stranger.issue_device_cert(me.node_id, me.public_key, "laptop")
    person_identity.save_profile(device_cert=cert, person_id=stranger.person_id)
    assert directory.build_record() is None


def test_directory_degrades_quietly_without_atlas(data_dir, monkeypatch):
    """Every directory call must be a no-op when the cluster is absent — the
    roster is local and authoritative, so discovery is the only thing lost."""
    from backend import atlas
    from backend.modules.social import directory

    monkeypatch.setattr(atlas, "collection", lambda _name: None)
    assert asyncio.run(directory.publish()) is False
    assert asyncio.run(directory.lookup("whoever")) == []
    asyncio.run(directory.unpublish())  # must not raise


def test_this_machine_registers_itself_as_a_device(data_dir, hub):
    """First boot must record this machine under its own person, even though the
    person key does not exist until something asks for it."""
    roster.register(hub)
    profile = roster.self_profile()
    assert len(profile.devices) == 1
    assert profile.devices[0].person_id == profile.person_id
