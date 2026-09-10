"""
Bullet smoke channels — CS2 mechanic where shooting through smoke punches
temporary visibility tunnels through the cloud.

Tests cover:
1. Zone channel storage and expiry
2. `sight_blocked_by` respecting active channels (bypassing smoke)
3. `snapshot()` serialising channels onto the wire
"""
import time

import pytest

from backend.modules.hassault.grenades import Zone, sight_blocked_by


def make_smoke(
    x: float = 10.0,
    y: float = 10.0,
    z: float = 1.5,
    radius: float = 4.0,
    remaining: float = 15.0,
    duration: float = 18.0,
) -> Zone:
    return Zone(
        id=1,
        kind="smoke",
        owner="test-player",
        team=0,
        x=x,
        y=y,
        z=z,
        radius=radius,
        remaining=remaining,
        duration=duration,
        damage_per_second=0.0,
    )


class TestZoneChannels:
    """Channel field on Zone: creation, expiry, serialisation."""

    def test_channels_empty_by_default(self) -> None:
        zone = make_smoke()
        assert zone.channels == []

    def test_channel_appended(self) -> None:
        zone = make_smoke()
        now = time.time()
        zone.channels.append((10.5, 10.0, 1.5, 1.2, now + 0.8))
        assert len(zone.channels) == 1

    def test_channels_in_snapshot_when_active(self) -> None:
        zone = make_smoke()
        now = time.time()
        zone.channels.append((10.5, 10.0, 1.5, 1.2, now + 10.0))
        snap = zone.snapshot()
        assert "channels" in snap
        assert len(snap["channels"]) == 1
        # Each channel on the wire is [x, y, z, remaining_time]
        ch = snap["channels"][0]
        assert len(ch) == 4

    def test_expired_channels_excluded_from_snapshot(self) -> None:
        zone = make_smoke()
        # Channel with expiry in the past (remaining <= 0)
        zone.channels.append((10.5, 10.0, 1.5, 1.2, -1.0))
        snap = zone.snapshot()
        # Either no key or empty — expired channels must not appear on the wire
        assert snap.get("channels") is None or len(snap.get("channels", [])) == 0

    def test_snapshot_limits_to_8_channels(self) -> None:
        zone = make_smoke()
        now = time.time()
        for i in range(12):
            zone.channels.append((10.0 + i * 0.1, 10.0, 1.5, 1.2, now + 10.0))
        snap = zone.snapshot()
        # At most 8 channels on the wire
        assert len(snap["channels"]) <= 8


class TestSightBlockedByWithChannels:
    """sight_blocked_by must respect bullet channels punched through smoke."""

    def test_smoke_blocks_normally(self) -> None:
        """Baseline: a smoke cloud between two points blocks the sightline."""
        zone = make_smoke(x=10.0, y=10.0, z=1.5, radius=4.0)
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert sight_blocked_by([zone], a, b, now=time.time()) is True

    def test_channel_unblocks_sightline(self) -> None:
        """A bullet channel through the smoke should let the sightline pass."""
        zone = make_smoke(x=10.0, y=10.0, z=1.5, radius=4.0)
        now = time.time()
        # Channel right on the ray from (5, 10, 1.5) -> (15, 10, 1.5)
        # The channel is at (10, 10, 1.5) with radius 1.2, expiring in 10s
        zone.channels.append((10.0, 10.0, 1.5, 1.2, now + 10.0))
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert sight_blocked_by([zone], a, b, now=now) is False

    def test_expired_channel_does_not_unblock(self) -> None:
        """Once the channel expires, the smoke should block again."""
        zone = make_smoke(x=10.0, y=10.0, z=1.5, radius=4.0)
        now = time.time()
        # Channel with expiry in the past
        zone.channels.append((10.0, 10.0, 1.5, 1.2, now - 1.0))
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert sight_blocked_by([zone], a, b, now=now) is True

    def test_channel_far_from_ray_does_not_unblock(self) -> None:
        """A channel that is not near the ray should not unblock."""
        zone = make_smoke(x=10.0, y=10.0, z=1.5, radius=4.0)
        now = time.time()
        # Channel offset far from the ray
        zone.channels.append((10.0, 14.0, 1.5, 1.2, now + 10.0))
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert sight_blocked_by([zone], a, b, now=now) is True

    def test_no_channels_means_always_blocked(self) -> None:
        """Smoke with no channels should always block."""
        zone = make_smoke(x=10.0, y=10.0, z=1.5, radius=4.0)
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert zone.channels == []
        assert sight_blocked_by([zone], a, b, now=time.time()) is True

    def test_fire_zone_never_blocks_sight(self) -> None:
        """Only smoke zones block; fire zones don't even if they have channels."""
        zone = Zone(
            id=2,
            kind="fire",
            owner="test-player",
            team=0,
            x=10.0,
            y=10.0,
            z=1.5,
            radius=4.0,
            remaining=10.0,
            duration=10.0,
            damage_per_second=40.0,
        )
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert sight_blocked_by([zone], a, b, now=time.time()) is False

    def test_multiple_channels_one_matches(self) -> None:
        """If any channel matches the ray, sight is unblocked."""
        zone = make_smoke(x=10.0, y=10.0, z=1.5, radius=4.0)
        now = time.time()
        # One channel far away, one on the ray
        zone.channels.append((10.0, 14.0, 1.5, 1.2, now + 10.0))  # too far
        zone.channels.append((10.0, 10.0, 1.5, 1.2, now + 10.0))  # on ray
        a = (5.0, 10.0, 1.5)
        b = (15.0, 10.0, 1.5)
        assert sight_blocked_by([zone], a, b, now=now) is False
