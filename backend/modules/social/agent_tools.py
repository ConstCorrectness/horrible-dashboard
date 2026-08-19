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
from backend.modules.social import handles, roster, store
from backend.modules.social.friendcode import is_friend_code, parse_friend_code
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


def _resolve(who: str) -> dict[str, Any] | None:
    """Find a roster row by friend code, person id, or (case-insensitive) name.

    Name matching is last and exact-ish on purpose: silently picking the closest
    fuzzy match would let the agent message the wrong person.

    Sync, and therefore **no `@username`** — that needs a directory round trip.
    Use `resolve_row` unless you are already on a sync path.
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


async def resolve_row(who: str) -> dict[str, Any] | None:
    """`_resolve`, plus `@username`.

    The handle branch runs **first** and is exact: a username is globally unique
    and the directory entry is checked against its own key fingerprint, so it is a
    stronger name than a display name and must not lose to one.

    A username only names someone already in the roster here — this resolves *who
    you meant*, not *who exists*. Adding a stranger by username is `add_friend`.
    """
    who = (who or "").strip()
    if handles.is_handle(who):
        entry = await handles.resolve(who)
        return store.get_friend_row(str(entry["person_id"])) if entry else None
    return _resolve(who)


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
    row = await resolve_row(who)
    if row is None:
        return {
            "error": f"no friend matching {who!r} — try their @username or friend code"
        }
    if row["status"] != "accepted":
        return {"error": f"{row['display_name']} is not an accepted friend yet"}

    # Routed through the chat manager rather than straight onto the wire, so an
    # agent-sent message lands in the same conversation the panel shows — and is
    # addressed to the *person*, so the manager picks whichever of their machines
    # is up rather than the agent guessing at a node.
    try:
        sent = await chat_manager.send_to_person(row["person_id"], text)
    except KeyError:
        return {"error": f"{row['display_name']} has no machine online right now"}
    return {"ok": True, "sent_to": row["display_name"], "via_node": sent["nodeId"]}


async def ask_friend_agent(args: dict[str, Any]) -> dict[str, Any]:
    """Ask a friend's agent a question and wait for its answer.

    Gated on the far side by that node's `network.allowRemoteAgent` /
    `remoteAgentMode` settings — being friends grants reachability, not authority.
    """
    who = str(args.get("who") or "")
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "no question to ask"}
    row = await resolve_row(who)
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
                "description": (
                    "Who to reach: an @username (best — globally unique), a friend "
                    "code (HD-XXXX-...), a person id, or their exact display name."
                ),
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
                "description": (
                    "Who to reach: an @username (best — globally unique), a friend "
                    "code (HD-XXXX-...), a person id, or their exact display name."
                ),
            },
            "prompt": {"type": "string", "description": "The question to ask."},
        },
        required=["who", "prompt"],
        side_effect=True,
    )
