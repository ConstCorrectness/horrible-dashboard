"""The miss limiter that makes a 40-bit watch code safe to hand out."""

from __future__ import annotations

from backend.share_relay import ratelimit
from backend.share_relay.ratelimit import MissLimiter


def test_blocks_after_the_limit_and_not_before() -> None:
    limiter = MissLimiter(limit=3, window_s=60)
    for _ in range(2):
        limiter.miss("a", now=0)
    assert limiter.blocked("a", now=0) is False
    limiter.miss("a", now=0)
    assert limiter.blocked("a", now=0) is True


def test_addresses_are_independent() -> None:
    limiter = MissLimiter(limit=1, window_s=60)
    limiter.miss("a", now=0)
    assert limiter.blocked("a", now=0) is True
    assert limiter.blocked("b", now=0) is False


def test_the_window_slides() -> None:
    # A minute of patience unblocks; an address is refused, not banned.
    limiter = MissLimiter(limit=2, window_s=60)
    limiter.miss("a", now=0)
    limiter.miss("a", now=30)
    assert limiter.blocked("a", now=59) is True
    assert limiter.blocked("a", now=61) is False
    assert limiter.blocked("a", now=91) is False


def test_memory_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "MAX_TRACKED", 10)
    limiter = MissLimiter(limit=5, window_s=60)
    for i in range(50):
        limiter.miss(f"addr-{i}", now=0)
    assert len(limiter._misses) <= 10
