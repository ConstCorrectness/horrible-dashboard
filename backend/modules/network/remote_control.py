"""Remote control: execute commands received from trusted peers (e.g. mobile app).

Trusted peers can send `remote_command` envelopes to trigger local actions like
playing media, opening panes, or triggering agent tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.modules.network import protocol

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)


async def handle_remote_command(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    """Execute a command from a trusted peer."""
    if not session.info.trusted:
        logger.warning("dropping remote command from untrusted peer %s", env.src)
        return

    data = env.data
    cmd = str(data.get("command", ""))
    params = data.get("params", {})

    logger.info("remote command from %s: %s(%s)", env.src, cmd, params)

    if cmd == "open_pane":
        from backend.modules.ws import broadcast_event
        pane_id = str(params.get("pane_id", ""))
        if pane_id:
            await broadcast_event("layout", "open_pane", {"paneId": pane_id})
            
    elif cmd == "play_media":
        from backend.modules.ws import broadcast_event
        url = str(params.get("url", ""))
        title = str(params.get("title", "Media"))
        if url:
            # Open browser pane with the media URL
            await broadcast_event("layout", "open_pane", {
                "paneId": "browser.view",
                "params": {"url": url, "title": title}
            })
            await broadcast_event("mobile", "media_status", {"playing": True, "title": title})

    elif cmd == "say":
        # Agent TTS or notification
        text = str(params.get("text", ""))
        from backend.modules.ws import broadcast_event
        await broadcast_event("agent", "notification", {"text": text})

    # Reply with ack
    await hub.send_to(
        env.src,
        protocol.AUTH_RESULT, # Or use a generic ack if we add one
        {"ok": True},
        re=env.msg_id,
    )
