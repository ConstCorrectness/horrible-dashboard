"""The relay's ICE configuration.

This exists because of a failure with no symptom: a relay that gathers only host
candidates answers every WHIP offer with a perfectly valid SDP, reports the
stream live, and never carries a frame. It works flawlessly on one machine --
where a host candidate is the right answer -- and never in production.
"""

from __future__ import annotations

import pytest

from backend.share_relay import ice


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "SHARE_RELAY_STUN",
        "SHARE_RELAY_TURN_URL",
        "SHARE_RELAY_TURN_USER",
        "SHARE_RELAY_TURN_PASS",
        "SHARE_RELAY_TURN_FOR_VIEWERS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_stun_is_on_by_default() -> None:
    """The out-of-the-box deploy must gather a reflexive candidate. Defaulting to
    nothing would make "it deployed fine and shows no video" the default."""
    assert ice.stun_server() == ice.DEFAULT_STUN
    assert ice.viewer_ice() == [{"urls": [f"stun:{ice.DEFAULT_STUN}"]}]


def test_the_stun_scheme_is_added_not_expected(monkeypatch) -> None:
    # Same semantics as the frontend's `buildIceConfig` and the node's
    # `_ice_servers`: bare host:port in, `stun:` added here. Three readers of one
    # idea that disagreed about the scheme would fail as "ICE does not connect".
    monkeypatch.setenv("SHARE_RELAY_STUN", "stun.example.com:3478")
    assert ice.viewer_ice()[0]["urls"] == ["stun:stun.example.com:3478"]


def test_stun_can_be_disabled_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_STUN", "")
    assert ice.stun_server() == ""
    assert ice.viewer_ice() == []


def test_turn_needs_every_part(monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    assert ice.turn_is_incomplete() is True

    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    assert ice.turn_is_incomplete() is True

    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")
    assert ice.turn_is_incomplete() is False


def test_viewers_do_not_get_turn_by_default(monkeypatch) -> None:
    """A TURN credential in a page anyone can open makes the operator's bandwidth
    free for the whole internet, so it is opt-in."""
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")

    assert all("turn" not in str(entry) for entry in ice.viewer_ice())

    monkeypatch.setenv("SHARE_RELAY_TURN_FOR_VIEWERS", "1")
    assert any("turn" in str(entry) for entry in ice.viewer_ice())


def test_describe_reports_presence_never_the_credential(monkeypatch) -> None:
    # `/health` is unauthenticated for reads in practice; it must answer "is TURN
    # configured" without answering "what is the password".
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "relayuser")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "hunter2")

    described = ice.describe()
    assert described["turn"] is True
    assert "hunter2" not in str(described)
    assert "relayuser" not in str(described)


def test_ice_servers_builds_real_aiortc_objects(monkeypatch) -> None:
    pytest.importorskip("aiortc", reason="needs the `webrtc` extra")
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")

    servers = ice.ice_servers()
    assert [s.urls for s in servers] == [
        [f"stun:{ice.DEFAULT_STUN}"],
        ["turn:relay.example.com:3478"],
    ]
    assert servers[1].username == "u"


def test_incomplete_turn_is_dropped_rather_than_offered(monkeypatch) -> None:
    # A malformed entry is worse than a missing one: it can take the whole
    # configuration down with it rather than degrading to STUN.
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    servers = ice.ice_servers()
    assert len(servers) == 1
    assert servers[0].urls == [f"stun:{ice.DEFAULT_STUN}"]
