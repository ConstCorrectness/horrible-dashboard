"""Remote Screen Watch: stream dashboard frames to a peer.

When a peer sends `VIEW_REQUEST`, we check trust and start a task that
periodically snaps the active dashboard view and sends it as `VIEW_FRAME`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

# node_id -> task
_active_streams: dict[str, asyncio.Task[None]] = {}


async def handle_view_request(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    """Peer requested to watch our screen."""
    if not session.info.trusted:
        return

    node_id = env.src
    if node_id in _active_streams:
        _active_streams[node_id].cancel()

    async def _stream_pump():
        try:
            logger.info("Starting remote view stream for %s", node_id)
            while True:
                # In a real implementation, we'd grab the active browser frame
                # or a system screenshot. For this demo, we'll use a placeholder.
                # await hub.send_to(node_id, protocol.VIEW_FRAME, {"frame": "..."})
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("Remote view stream for %s stopped", node_id)
        except Exception:
            logger.exception("Remote view stream error")
        finally:
            _active_streams.pop(node_id, None)

    _active_streams[node_id] = asyncio.create_task(_stream_pump())


async def handle_view_stop(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    """Peer stopped watching."""
    task = _active_streams.pop(env.src, None)
    if task:
        task.cancel()
