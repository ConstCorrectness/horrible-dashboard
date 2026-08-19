"""Agent tools for standing instructions: watches and mutes.

This is what makes *"let me know when Andrew logs in, and mute any messages except
for him for a bit"* a thing the agent can actually carry out rather than
acknowledge and forget. Nothing in the app could hold an instruction past the end
of a turn before this — no watch table, no rule engine, no cron.

**Two conventions here are load-bearing and fail silently if broken.**

1. **A tool's group is its name prefix, not its `group=` field.** The orchestrator
   computes it with `name.split(".", 1)[0]` (`agent/orchestrator._group_of`). A tool
   called `watch.create` carrying `group="notifications"` lands in group `watch`
   regardless, and the `group=` value only decides core-vs-deferred. So the names
   here are `watch.*` and `notify.*`, and they declare the matching group.

2. **Every one of these is grouped, never core.** The always-loaded core is 11
   tools against a budget of 38, because local models stop reasoning past roughly
   40 tool definitions. Six always-on tools for a feature used occasionally would
   cost every turn in the app.

`_resolve` from the social module does the name→person work — it already accepts a
username, friend code or display name and refuses an ambiguous one rather than
guessing, which is exactly the behaviour "when Andrew logs in" needs.
"""

from __future__ import annotations

import time
from typing import Any

from backend.modules.notifications import store
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

GROUP = "watch"
NOTIFY_GROUP = "notify"


def _person(who: str) -> tuple[str | None, str, str | None]:
    """`(person_id, label, error)` for whatever the user called someone.

    Reuses the social module's resolver so "Andrew" means the same person to a
    watch as it does to `social.message`.
    """
    from backend.modules.social.agent_tools import _resolve

    row = _resolve(who)
    if row is None:
        return None, who, f"no one in your roster matching {who!r}"
    return str(row["person_id"]), str(row["display_name"] or who), None


def _duration(spec: Any) -> float | None:
    """Minutes as a number, or None. Kept generous and forgiving: the model is
    turning "for a bit" into something, and rejecting an odd value would fail the
    whole instruction over the least important part of it."""
    try:
        minutes = float(spec)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    # A day is the ceiling. A mute that outlives the user remembering they set one
    # is indistinguishable from the app being broken.
    return min(minutes, 24 * 60) * 60.0


# ---- watch.* ----------------------------------------------------------------------


def watch_create(args: dict[str, Any]) -> dict[str, Any]:
    who = str(args.get("who") or "").strip()
    if not who:
        return {"error": "name someone to watch for"}
    person_id, label, error = _person(who)
    if person_id is None:
        return {"error": error or f"no one matching {who!r}"}
    online = bool(args.get("online", True))
    watch = store.add_watch(
        "presence",
        subject=person_id,
        label=label,
        predicate={"online": online},
        note=str(args.get("note") or "") or None,
        one_shot=bool(args.get("one_shot", True)),
        duration_s=_duration(args.get("expires_in_minutes")),
    )
    return {
        "ok": True,
        "watch_id": watch["id"],
        "watching": label,
        "for": "coming online" if online else "going offline",
    }


def watch_list(_args: dict[str, Any]) -> dict[str, Any]:
    watches = store.list_watches()
    return {
        "watches": [
            {
                "id": w["id"],
                "watching": w["label"],
                "for": "coming online"
                if w["predicate"].get("online", True)
                else "going offline",
                "one_shot": bool(w["one_shot"]),
                "expires_in_minutes": round((w["expires_at"] - time.time()) / 60)
                if w["expires_at"]
                else None,
            }
            for w in watches
        ]
    }


def watch_cancel(args: dict[str, Any]) -> dict[str, Any]:
    watch_id = str(args.get("watch_id") or "")
    if not watch_id:
        return {"error": "watch_id is required"}
    return {"ok": store.cancel_watch(watch_id)}


# ---- notify.* ---------------------------------------------------------------------


def notify_mute(args: dict[str, Any]) -> dict[str, Any]:
    category = str(args.get("category") or "all")
    if category not in store.CATEGORIES:
        return {
            "error": f"category must be one of {', '.join(store.CATEGORIES)}",
        }
    person_id = except_person = None
    label = "everyone"
    if who := str(args.get("who") or "").strip():
        person_id, label, error = _person(who)
        if person_id is None:
            return {"error": error or f"no one matching {who!r}"}
    if other := str(args.get("except_who") or "").strip():
        except_person, except_label, error = _person(other)
        if except_person is None:
            return {"error": error or f"no one matching {other!r}"}
        label = f"everyone except {except_label}"
    minutes = _duration(args.get("duration_minutes"))
    store.add_mute(
        category,
        person_id=person_id,
        except_person=except_person,
        duration_s=minutes,
        reason=str(args.get("reason") or "") or None,
    )
    return {
        "ok": True,
        "muted": category,
        "who": label,
        "for_minutes": round(minutes / 60) if minutes else None,
    }


def notify_unmute(args: dict[str, Any]) -> dict[str, Any]:
    category = str(args.get("category") or "") or None
    person_id = None
    if who := str(args.get("who") or "").strip():
        person_id, _label, error = _person(who)
        if person_id is None:
            return {"error": error or f"no one matching {who!r}"}
    return {"ok": True, "lifted": store.clear_mutes(category, person_id)}


def notify_status(_args: dict[str, Any]) -> dict[str, Any]:
    """What is currently silenced, and why — so "why didn't I hear about that?" has
    an answer that isn't guesswork."""
    now = time.time()
    return {
        "mutes": [
            {
                "category": m["category"],
                "person_id": m["person_id"],
                "except_person": m["except_person"],
                "reason": m["reason"],
                "expires_in_minutes": round((m["expires_at"] - now) / 60)
                if m["expires_at"]
                else None,
            }
            for m in store.active_mutes()
        ]
    }


def register_notification_tools() -> None:
    tools = [
        AgentTool(
            name="watch.create",
            description=(
                "Tell the user when someone comes online (or goes offline). The "
                "watch persists until it fires, expires, or is cancelled — use it "
                "for 'let me know when X logs in'."
            ),
            handler=watch_create,
            parameters={
                "who": {
                    "type": "string",
                    "description": "@username, friend code, or display name.",
                },
                "online": {
                    "type": "boolean",
                    "description": "True to watch for arriving (default), false for leaving.",
                },
                "one_shot": {
                    "type": "boolean",
                    "description": "Fire once then remove (default true).",
                },
                "expires_in_minutes": {"type": "number"},
                "note": {
                    "type": "string",
                    "description": "What to say when it fires; the user's own words work well.",
                },
            },
            required=["who"],
            side_effect=True,
            group=GROUP,
        ),
        AgentTool(
            name="watch.list",
            description="The standing watches currently set.",
            handler=watch_list,
            group=GROUP,
        ),
        AgentTool(
            name="watch.cancel",
            description="Cancel a watch by id (see watch.list).",
            handler=watch_cancel,
            parameters={"watch_id": {"type": "string"}},
            required=["watch_id"],
            side_effect=True,
            group=GROUP,
        ),
        AgentTool(
            name="notify.mute",
            description=(
                "Silence notifications. Scope by category, by person, or by "
                "'everyone except one person' (except_who) — that last shape is "
                "what 'mute everything except Andrew' needs. Give a duration for "
                "'for a bit'."
            ),
            handler=notify_mute,
            parameters={
                "category": {
                    "type": "string",
                    "enum": list(store.CATEGORIES),
                    "description": "What to silence; 'all' for everything.",
                },
                "who": {"type": "string", "description": "Mute only this person."},
                "except_who": {
                    "type": "string",
                    "description": "Mute everyone BUT this person.",
                },
                "duration_minutes": {"type": "number"},
                "reason": {"type": "string"},
            },
            side_effect=True,
            group=NOTIFY_GROUP,
        ),
        AgentTool(
            name="notify.unmute",
            description="Lift mutes. No arguments lifts all of them.",
            handler=notify_unmute,
            parameters={
                "category": {"type": "string", "enum": list(store.CATEGORIES)},
                "who": {"type": "string"},
            },
            side_effect=True,
            group=NOTIFY_GROUP,
        ),
        AgentTool(
            name="notify.status",
            description="What is currently muted, and for how much longer.",
            handler=notify_status,
            group=NOTIFY_GROUP,
        ),
    ]
    for tool in tools:
        registry.agent_tools[tool.name] = tool
