"""Agent tools for the `share` module.

These let the agent run the *social* side of a session — "share my workspace with
Rob", "who's watching?", "drop everyone back to view-only" — which is exactly the
part that is tedious mid-flow for a human and trivial for an agent.

They deliberately stop short of **granting anything above `view`**. Raising a
guest to `terminal` or `control` is a decision about how much of your machine
somebody else gets, and a model that can make it from a sentence is a model that
can be talked into making it. `share.revoke_all` is available and `share.grant`
is not, because the asymmetry is the point: the agent can always take rights
away, never hand them out.

The tool prefix is `share`, matching the module id, because the orchestrator
groups tools by name prefix — `AgentTool.group` is a flag, not the group name.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.share import fabric
from backend.modules.share.session import list_invitees, share_manager
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


async def status(_args: dict[str, Any]) -> dict[str, Any]:
    """What is being shared right now, from both directions."""
    snap = share_manager.snapshot()
    hosting = snap.hosting
    return {
        "hosting": None
        if hosting is None
        else {
            "id": hosting.id,
            "title": hosting.title,
            "mode": hosting.mode,
            "participants": [
                {"name": p.name, "grant": p.grant, "role": p.role}
                for p in hosting.participants
            ],
            "public_link": hosting.link or None,
        },
        "joined": [
            {"id": s.id, "title": s.title, "host": s.host_name, "grant": s.grant}
            for s in snap.joined
        ],
        "invites": [
            {"id": i.session_id, "title": i.title, "from": i.host_name}
            for i in snap.invites
        ],
    }


async def start(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "")
    session = await share_manager.start(title, "semantic")
    return {"ok": True, "id": session.id, "title": session.title}


async def stop(_args: dict[str, Any]) -> dict[str, Any]:
    if share_manager.hosting is None:
        return {"ok": False, "error": "no session is running"}
    await share_manager.stop()
    return {"ok": True}


async def invite(args: dict[str, Any]) -> dict[str, Any]:
    """Invite a friend by name, username or friend code.

    Starts a session first when none is running: "share my workspace with Rob" is
    one intention, and making the agent call two tools to express it only creates
    a state where the first succeeded and the second did not.
    """
    who = str(args.get("who") or "").strip()
    if not who:
        return {"ok": False, "error": "name a friend to invite"}

    needle = who.lstrip("@").lower()
    candidates = await list_invitees()
    match = next(
        (
            c
            for c in candidates
            if needle in (c.name.lower(), c.username.lower(), c.friend_code.lower())
        ),
        None,
    ) or next(
        (c for c in candidates if needle in c.name.lower()),
        None,
    )
    if match is None:
        return {
            "ok": False,
            "error": f"no friend matching {who!r} has a machine online",
            "online": [c.name for c in candidates],
        }
    if not match.can_share:
        return {
            "ok": False,
            "error": f"{match.name} is online but their app cannot join a session yet",
        }

    session = share_manager.hosting or await share_manager.start(
        str(args.get("title") or ""), "semantic"
    )
    sent = await fabric.invite_person(match.person_id, session.id, session.title)
    return {
        "ok": True,
        "invited": match.name,
        "session": session.id,
        # An offline machine queues rather than fails, and the difference matters
        # to whoever is waiting for someone to appear.
        "delivered": sent,
        "queued": sent == 0,
    }


async def revoke_all(_args: dict[str, Any]) -> dict[str, Any]:
    if share_manager.hosting is None:
        return {"ok": False, "error": "no session is running"}
    await share_manager.revoke_all()
    return {"ok": True, "note": "everyone is back to view-only; the session is open"}


def register_share_tools() -> None:
    registry.agent_tools["share.status"] = AgentTool(
        name="share.status",
        description=(
            "What this node is sharing right now: the session it hosts and who is "
            "in it, sessions it has joined, and pending invitations."
        ),
        handler=status,
        group="share",
    )
    registry.agent_tools["share.start"] = AgentTool(
        name="share.start",
        description=(
            "Open a shared session so friends can be invited to watch this "
            "workspace. Guests join view-only."
        ),
        handler=start,
        group="share",
        side_effect=True,
        parameters={
            "title": {
                "type": "string",
                "description": "What to call the session, e.g. 'debugging the crawler'.",
            }
        },
    )
    registry.agent_tools["share.stop"] = AgentTool(
        name="share.stop",
        description="End the shared session this node is hosting.",
        handler=stop,
        group="share",
        side_effect=True,
    )
    registry.agent_tools["share.invite"] = AgentTool(
        name="share.invite",
        description=(
            "Invite a friend to this node's shared session, starting one if none "
            "is running. They join view-only. Accepts a name, @username or friend "
            "code."
        ),
        handler=invite,
        group="share",
        side_effect=True,
        parameters={
            "who": {
                "type": "string",
                "description": "The friend: their name, @username or friend code.",
            },
            "title": {
                "type": "string",
                "description": "Title for the session, if one is being started.",
            },
        },
        required=["who"],
    )
    registry.agent_tools["share.revoke_all"] = AgentTool(
        name="share.revoke_all",
        description=(
            "Drop every guest back to view-only without ending the session. Use "
            "when asked to stop people editing, typing or controlling anything."
        ),
        handler=revoke_all,
        group="share",
        side_effect=True,
    )
