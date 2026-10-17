"""Broadcasts on the `music` `/ws` channel.

The channel is a public contract (plugin widgets subscribe to it, see
docs/architecture/plugin-sdk.mdx):

- `state`    the whole `PlayerState`, on every session mutation (revisioned);
- `progress` `{position, duration}`, ~1 Hz from the client playing the audio;
- `track`    one library row changed (download progress, an edit, enrichment);
- `job`      an import job's counters changed;
- `library`  `{removed: [ids]}` / `{changed: true}` for bulk changes.
"""

from __future__ import annotations

from typing import Any

from backend.modules.ws import broadcast_event

CHANNEL = "music"


async def publish(event: str, data: dict[str, Any]) -> None:
    await broadcast_event(CHANNEL, event, data)


async def publish_track(track: dict[str, Any]) -> None:
    from backend.modules.music.models import TrackModel  # noqa: PLC0415 — light import path

    await publish("track", TrackModel(**track).model_dump())
