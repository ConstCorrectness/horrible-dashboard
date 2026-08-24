"""The relay's token rules.

These are the whole of a public viewer's authority, so they are tested at the
level of "what can a stranger holding a URL learn or do", not just "does the
dataclass round-trip".
"""

from __future__ import annotations

import time

from backend.share_relay.tokens import DEFAULT_TTL_S, MAX_TTL_S, Registry


def test_minted_tokens_are_unique_and_long() -> None:
    registry = Registry()
    tokens = {registry.mint().token for _ in range(50)}
    assert len(tokens) == 50
    # 32 urlsafe bytes base64s to 43 chars. A short token is a guessable one.
    assert all(len(t) >= 40 for t in tokens)


def test_revoked_and_expired_are_indistinguishable_from_unknown() -> None:
    # Collapsing the three is deliberate: telling a caller "that existed but
    # expired" confirms the token was real, which is one bit more than someone
    # holding a guessed URL should learn.
    registry = Registry()
    live = registry.mint()
    registry.revoke(live.token)
    expired = registry.mint(ttl_s=60)

    assert registry.get(live.token) is None
    assert registry.get("never-existed") is None
    assert registry.get(expired.token, now=time.time() + 120) is None


def test_revoke_is_immediate_and_idempotent() -> None:
    registry = Registry()
    stream = registry.mint()
    assert registry.get(stream.token) is not None
    assert registry.revoke(stream.token) is True
    assert registry.get(stream.token) is None
    # Second revoke reports "nothing to do" rather than claiming a fresh kill.
    assert registry.revoke(stream.token) is False


def test_ttl_is_clamped_in_both_directions() -> None:
    registry = Registry()
    now = time.time()
    forever = registry.mint(ttl_s=10**9, now=now)
    instant = registry.mint(ttl_s=1, now=now)
    default = registry.mint(now=now)

    assert forever.expires_at == now + MAX_TTL_S
    assert instant.expires_at == now + 60
    assert default.expires_at == now + DEFAULT_TTL_S


def test_passphrase_is_hashed_not_stored() -> None:
    registry = Registry()
    stream = registry.mint(passphrase="hunter2")
    assert "hunter2" not in stream.passphrase_hash
    assert stream.check_passphrase("hunter2") is True
    assert stream.check_passphrase("hunter3") is False
    assert stream.check_passphrase("") is False


def test_no_passphrase_admits_everyone() -> None:
    # An unprotected link is the common case; it must not accidentally require
    # the empty string to match something.
    stream = Registry().mint()
    assert stream.check_passphrase("") is True
    assert stream.check_passphrase("anything") is True


def test_same_passphrase_hashes_differently_per_stream() -> None:
    # Per-stream salt: two hosts using the same obvious passphrase must not
    # produce the same hash, or one leak reveals both.
    registry = Registry()
    a = registry.mint(passphrase="open")
    b = registry.mint(passphrase="open")
    assert a.passphrase_hash != b.passphrase_hash


def test_sweep_drops_only_dead_entries() -> None:
    registry = Registry()
    keep = registry.mint(ttl_s=3600)
    gone = registry.mint(ttl_s=3600)
    registry.revoke(gone.token)

    assert registry.sweep() == 1
    assert len(registry) == 1
    assert registry.get(keep.token) is not None


def test_viewer_ceiling_is_enforced_per_stream() -> None:
    # An aiortc SFU has a real ceiling. Refusing the next viewer plainly beats
    # accepting them and degrading the stream for everyone already watching.
    registry = Registry(max_viewers_per_stream=2)
    stream = registry.mint()
    assert registry.at_capacity(stream) is False
    stream.viewers = 2
    assert registry.at_capacity(stream) is True


def test_title_is_bounded() -> None:
    stream = Registry().mint(title="x" * 500)
    assert len(stream.title) == 120
