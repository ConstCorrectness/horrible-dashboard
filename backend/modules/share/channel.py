"""The `/ws` `share` channel: live session state for the share pane.

Outbound events are broadcast to **every** tab rather than fanned per-connection,
because the session is process-global: two tabs on one machine are two renderers
of one session, not two sessions.

Inbound mutations are dispatched with `create_task` rather than awaited. Each one
dials or messages a peer, and a handler that blocks this receive loop would stall
every other channel on the same socket — the rule `social/channel.py` states.

Media never rides this channel. `signal` carries SDP and ICE only, which is a few
hundred bytes per negotiation; the pixels go browser-to-browser over WebRTC and
never reach the backend at all.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.modules.share import fabric
from backend.modules.share.models import GrantLevel, ShareMode
from backend.modules.share.session import CHANNEL, share_manager


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": CHANNEL, "event": event, "data": data}


async def handle_share_message(conn: Any, msg: dict[str, Any]) -> None:
    """Route an inbound `share`-channel message from the browser."""
    event = msg.get("event")
    data = msg.get("data") or {}

    if event == "state":
        await conn.send_json(_evt("state", share_manager.snapshot().model_dump()))
        # Replay whatever projections we hold, so a pane that mounts (or a tab
        # that reconnects) mid-session paints immediately rather than waiting for
        # the host to move something.
        for session_id, frame in share_manager.remote_mirrors.items():
            await conn.send_json(
                _evt("remote_mirror", {"sessionId": session_id, "frame": frame})
            )

    elif event == "start":
        mode: ShareMode = str(data.get("mode") or "semantic")  # type: ignore[assignment]
        asyncio.create_task(share_manager.start(str(data.get("title") or ""), mode))

    elif event == "stop":
        asyncio.create_task(share_manager.stop())

    elif event == "invite":
        person_id = str(data.get("personId") or "")
        hosting = share_manager.hosting
        if person_id and hosting is not None:
            asyncio.create_task(
                fabric.invite_person(person_id, hosting.id, hosting.title)
            )

    elif event == "grant":
        grant: GrantLevel = str(data.get("grant") or "view")  # type: ignore[assignment]
        asyncio.create_task(
            share_manager.set_grant(str(data.get("personId") or ""), grant)
        )

    elif event == "revoke_all":
        asyncio.create_task(share_manager.revoke_all())

    elif event == "kick":
        asyncio.create_task(
            share_manager.remove_participant(str(data.get("nodeId") or ""))
        )

    elif event == "join":
        asyncio.create_task(
            _join(
                conn,
                str(data.get("sessionId") or ""),
                str(data.get("hostNode") or ""),
            )
        )

    elif event == "leave":
        asyncio.create_task(fabric.leave_remote(str(data.get("sessionId") or "")))

    elif event == "dismiss_invite":
        share_manager.drop_invite(str(data.get("sessionId") or ""))

    elif event == "mirror":
        # The host's browser published a projection of its workspace. It arrives
        # already redacted — the redaction happens there because pane declarations
        # live in the frontend registry, and it happens on the *host's* machine
        # because the guest's is the untrusted end. Passed through untouched.
        frame = data.get("frame")
        summary = data.get("summary")
        if isinstance(frame, dict):
            asyncio.create_task(
                share_manager.set_mirror(
                    frame, summary if isinstance(summary, dict) else None
                )
            )

    elif event == "action":
        # A guest asking the host to do something. Relayed as-is; every decision
        # about it belongs to the host, and a check here would be a second,
        # weaker gate on the machine that has no say.
        asyncio.create_task(
            fabric.send_action(
                str(data.get("sessionId") or ""),
                str(data.get("name") or ""),
                data.get("params") if isinstance(data.get("params"), dict) else {},
            )
        )

    elif event == "signal":
        # Pass-through to one peer. Not inspected, not stored.
        asyncio.create_task(
            fabric.send_signal(str(data.get("to") or ""), data.get("payload"))
        )


async def _join(conn: Any, session_id: str, host_node: str) -> None:
    """Join a remote session, reporting failure to the tab that asked.

    The error goes back to the *asking* connection rather than being broadcast:
    a failed join is a fact about one person's click, not about the node.
    """
    if not session_id or not host_node:
        return
    ok, error = await fabric.join_remote(session_id, host_node)
    if not ok:
        try:
            await conn.send_json(
                _evt("error", {"sessionId": session_id, "message": error or "failed"})
            )
        except Exception:
            pass
