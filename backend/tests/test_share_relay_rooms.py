"""The relay's room table, without a media stack.

A room closes for more reasons than anyone asks it to: the WHIP DELETE, a revoke,
and -- the one that bit -- its own publisher's connection failing, which closes it
from inside aiortc's state handler. These pin what every one of those exits must
do, and none of them needs `aiortc` installed: an empty room closes without it.
"""

from __future__ import annotations

import pytest

from backend.share_relay.fanout import Rooms


@pytest.mark.anyio
async def test_a_room_that_closes_itself_is_forgotten() -> None:
    """The latched-`_closing` bug.

    A room that closed itself used to stay in the table with `_closing` set, so
    the host's next WHIP landed in a room whose `close()` was a no-op forever:
    its viewers could never be dropped, by a stop, a revoke, or anything else.
    """
    rooms = Rooms()
    room = rooms.get_or_create("tok")
    await room.close()  # as the publisher's failed-connection handler does
    assert rooms.get("tok") is None
    assert rooms.get_or_create("tok") is not room


@pytest.mark.anyio
async def test_every_close_is_reported_once() -> None:
    closed: list[str] = []

    async def on_close(token: str) -> None:
        closed.append(token)

    rooms = Rooms(on_close=on_close)
    rooms.get_or_create("a")
    room_b = rooms.get_or_create("b")

    await rooms.drop("a")
    await room_b.close()
    await room_b.close()  # re-entry, as a closing peer connection triggers

    assert closed == ["a", "b"]


@pytest.mark.anyio
async def test_a_stale_room_closing_does_not_evict_its_replacement() -> None:
    rooms = Rooms()
    old = rooms.get_or_create("tok")
    await rooms.drop("tok")
    new = rooms.get_or_create("tok")
    # The old room finishing a late close must not take the new one with it.
    await rooms._closed(old)
    assert rooms.get("tok") is new


@pytest.mark.anyio
async def test_a_failing_listener_does_not_fail_the_close() -> None:
    async def on_close(token: str) -> None:
        raise RuntimeError("nobody to tell")

    rooms = Rooms(on_close=on_close)
    rooms.get_or_create("tok")
    await rooms.drop("tok")  # must not raise
    assert rooms.get("tok") is None
