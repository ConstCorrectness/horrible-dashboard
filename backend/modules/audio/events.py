"""Broadcasts for the `audio` `/ws` channel.

The karaoke rule, applied to sound: **the server owns intent, the pane is a
renderer.** The mixer graph physically lives in the browser — only a Web Audio
context can actually move the samples — but the *routing* is server state, so a
change made by the agent, by a phone on the fabric, or by a second window reaches
every open mixer pane instead of only the one that made it.

Without this the agent's `audio.route` would write the database and the open
mixer pane would keep showing (and playing) the old routing until reloaded.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.ws import broadcast_event

logger = logging.getLogger(__name__)

CHANNEL = "audio"


async def publish_mixer(state: dict[str, Any]) -> None:
    """Tell every open pane the dashboard's routing matrix changed."""
    await broadcast_event(CHANNEL, "mixer", state)


async def publish_host(state: dict[str, Any] | None, error: str | None = None) -> None:
    """Tell every open pane the host (Voicemeeter) matrix changed.

    Also fires when the user moves a fader in Voicemeeter's own window — the
    poll in `poll_host` is what notices, and this is how the pane finds out.
    """
    await broadcast_event(CHANNEL, "host", {"state": state, "error": error})
