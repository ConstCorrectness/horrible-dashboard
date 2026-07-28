"""Agent tools over the friends roster.

These exist so the agent can act on *people* — "message Rob", "ask my laptop's
agent" — rather than making the user look up a 16-character node id first. Each
tool resolves a name or friend code to a person, then to whichever of their
machines is currently online.

The tool prefix is `social`, matching the module id, because the orchestrator
groups tools by name prefix.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.network import protocol
from backend.modules.network.chat import chat_manager
from backend.modules.network.hub import peer_hub
from backend.modules.social import identity as person_identity
from backend.modules.social import roster, store
from backend.modules.social.friendcode import is_friend_code, parse_friend_code
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


def _resolve(who: str) -> dict[str, Any] | None:
    """Find a roster row by friend code, person id, or (case-insensitive) name.

    Name matching is last and exact-ish on purpose: silently picking the closest
    fuzzy match would let the agent message the wrong person.
    """
    who = who.strip()
    if not who:
        return None
    if is_friend_code(who):
        return store.get_friend_row(parse_friend_code(who))
    exact = store.get_friend_row(who)
    if exact is not None:
        return exact
    lowered = who.lower()
    matches = [f for f in store.list_friends() if f.display_name.lower() == lowered]
    if len(matches) != 1:
        return None
    return store.get_friend_row(matches[0].person_id)


async def list_friends(_args: dict[str, Any]) -> dict[str, Any]:
    """Who is in the roster, and which of their machines are reachable."""
    friends = store.list_friends(roster.online_nodes())
    return {
        "friends": [
            {
                "name": f.display_name,
                "person_id": f.person_id,
                "friend_code": f.friend_code,
                "status": f.status,
                "presence": f.presence,
                "is_self": f.is_self,
                "devices": [
                    {"label": d.label, "node_id": d.node_id, "online": d.online}
                    for d in f.devices
                ],
            }
            for f in friends
        ],
        "you": person_identity.load_person().friend_code,
    }


async def message_friend(args: dict[str, Any]) -> dict[str, Any]:
    """Send a chat message to a friend over the peer wire."""
    who = str(args.get("who") or "")
    text = str(args.get("text") or "").strip()
    if not text:
        return {"error": "nothing to send"}
    row = _resolve(who)
    if row is None:
        return {"error": f"no friend matching {who!r} — try their friend code"}
    if row["status"] != "accepted":
        return {"error": f"{row['display_name']} is not an accepted friend yet"}

    nodes = roster.reachable_nodes(row["person_id"])
    if not nodes:
        return {"error": f"{row['display_name']} has no machine online right now"}
    # Routed through the chat manager rather than straight onto the wire, so an
    # agent-sent message lands in the same conversation history the panel shows.
    await chat_manager.send_to_peer(nodes[0], text)
    return {"ok": True, "sent_to": row["display_name"], "via_node": nodes[0]}


async def ask_friend_agent(args: dict[str, Any]) -> dict[str, Any]:
    """Ask a friend's agent a question and wait for its answer.

    Gated on the far side by that node's `network.allowRemoteAgent` /
    `remoteAgentMode` settings — being friends grants reachability, not authority.
    """
    who = str(args.get("who") or "")
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "no question to ask"}
    row = _resolve(who)
    if row is None:
        return {"error": f"no friend matching {who!r}"}
    if row["status"] != "accepted":
        return {"error": f"{row['display_name']} is not an accepted friend yet"}

    nodes = roster.reachable_nodes(row["person_id"])
    if not nodes:
        return {"error": f"{row['display_name']} has no machine online right now"}
    try:
        reply = await peer_hub.request(
            nodes[0], protocol.AGENT_REQUEST, {"prompt": prompt}, timeout=120.0
        )
    except TimeoutError:
        return {"error": f"{row['display_name']}'s agent did not answer in time"}
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "ok": True,
        "answered_by": row["display_name"],
        "answer": reply.data.get("answer") or reply.data.get("text"),
    }


def register_social_tools() -> None:
    registry.agent_tools["social.list_friends"] = AgentTool(
        name="social.list_friends",
        description=(
            "List the friends roster: each person, whether they are online, and "
            "their machines. Use this to resolve a name before messaging."
        ),
        handler=list_friends,
        group="social",
    )
    registry.agent_tools["social.message"] = AgentTool(
        name="social.message",
        description="Send a chat message to a friend by name or friend code.",
        handler=message_friend,
        group="social",
        parameters={
            "who": {
                "type": "string",
                "description": "Friend's name, person id, or friend code.",
            },
            "text": {"type": "string", "description": "The message to send."},
        },
        required=["who", "text"],
        side_effect=True,
    )
    registry.agent_tools["social.ask_agent"] = AgentTool(
        name="social.ask_agent",
        description=(
            "Ask a friend's agent a question and return its answer. The remote "
            "node decides whether to allow it and how much it may do."
        ),
        handler=ask_friend_agent,
        group="social",
        parameters={
            "who": {
                "type": "string",
                "description": "Friend's name, person id, or friend code.",
            },
            "prompt": {"type": "string", "description": "The question to ask."},
        },
        required=["who", "prompt"],
        side_effect=True,
    )
