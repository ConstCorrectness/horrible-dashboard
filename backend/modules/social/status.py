"""What you have set yourself to, and what you are doing: Steam's persona states.

Presence used to be the only signal, and it is derived — a person is online exactly
when one of their machines has a live session. That answers "is anyone there" but
not the two questions a friends list is for: *do they want to be bothered* and
*what are they up to*. This module adds both, as a layer **over** presence rather
than a replacement for it:

- **Persona** is chosen: `online`, `away`, `busy`, or `invisible`. It is stored per
  machine in the local profile, because it is a preference, not a fact about the
  fabric. `invisible` is never sent anywhere — friends are told `offline`, and their
  roster shows you exactly as it would if your machines were down.
- **Activity** is reported: "HorribleAssault · de_dust2". It is held in memory
  only, because an activity that survives a restart is a lie — nobody is still in
  the match the backend crashed out of.
- **Idle** is the browser's report that nobody has touched the app for a while. It
  turns a chosen `online` into a shared `away` without overwriting the choice, so
  coming back restores it with nothing to undo.

Both travel as `social_status` on the trusted peer wire, sent when either changes
and to each friend's machine as it connects. Never to strangers: what a person is
doing is for their friends.
"""

from __future__ import annotations

import time
from typing import Any, get_args

from backend.modules.social import identity as person_identity
from backend.modules.social.models import Activity, Persona, SharedPersona

PERSONAS: tuple[str, ...] = get_args(Persona)
SHARED_PERSONAS: tuple[str, ...] = get_args(SharedPersona)

#: Most available first. A person with a machine at `online` and another at `away`
#: is online — Steam's rule, and the one that answers "can I reach them".
_RANK = {"online": 3, "busy": 2, "away": 1, "offline": 0}

TITLE_MAX = 60
DETAIL_MAX = 120


def clean_activity(raw: Any) -> Activity | None:
    """An `Activity` from untrusted input — a browser message or a peer's — or None.

    Truncated rather than refused: an over-long map name is still a real activity,
    and the roster row has room for a line, not a paragraph.
    """
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()[:TITLE_MAX]
    if not title:
        return None
    detail = str(raw.get("detail") or "").strip()[:DETAIL_MAX] or None
    kind = raw.get("kind") if raw.get("kind") in ("game", "app") else "game"
    try:
        since = float(raw.get("since") or time.time())
    except (TypeError, ValueError):
        since = time.time()
    return Activity(kind=kind, title=title, detail=detail, since=since)


# ---- this machine ------------------------------------------------------------------

_activity: Activity | None = None
_idle = False


def own_persona() -> Persona:
    value = person_identity.load_profile().get("persona")
    return value if value in PERSONAS else "online"  # type: ignore[return-value]


def own_activity() -> Activity | None:
    return _activity


def own_idle() -> bool:
    return _idle


def set_persona(persona: str) -> bool:
    """Persist a chosen persona. Returns whether anything changed."""
    if persona not in PERSONAS or persona == own_persona():
        return False
    person_identity.save_profile(persona=persona)
    return True


def set_activity(raw: Any) -> bool:
    """Set (a dict) or clear (None) the current activity. Returns whether it changed.

    Re-reporting the same title and detail keeps the original `since`, so a client
    that re-announces its activity on every render does not reset "for 40 minutes"
    to zero each time.
    """
    global _activity
    new = clean_activity(raw) if raw is not None else None
    old = _activity
    if new is None and old is None:
        return False
    if new is not None and old is not None:
        if (new.kind, new.title, new.detail) == (old.kind, old.title, old.detail):
            return False
        if (new.kind, new.title) == (old.kind, old.title):
            new = new.model_copy(update={"since": old.since})
    _activity = new
    return True


def set_idle(idle: bool) -> bool:
    global _idle
    if bool(idle) == _idle:
        return False
    _idle = bool(idle)
    return True


def shared_persona() -> SharedPersona:
    """What friends are told. Idle demotes a chosen `online`, and nothing else."""
    persona = own_persona()
    if persona == "invisible":
        return "offline"
    if persona == "online" and _idle:
        return "away"
    return persona  # type: ignore[return-value]


def wire_payload() -> dict[str, Any]:
    """The `social_status` body. Invisible sends no activity: it would give you away."""
    persona = shared_persona()
    activity = _activity if persona != "offline" else None
    return {
        "persona": persona,
        "activity": activity.model_dump() if activity else None,
    }


# ---- other machines ----------------------------------------------------------------

#: node_id -> (shared persona, activity) as that machine last announced it. Held in
#: memory: it is only meaningful while the machine has a session, and a device with
#: no session counts as offline no matter what it last said.
_remote: dict[str, tuple[str, Activity | None]] = {}


def record_remote(node_id: str, data: dict[str, Any]) -> bool:
    """Store a peer's announcement. Returns whether it changed anything."""
    persona = data.get("persona")
    if persona not in SHARED_PERSONAS:
        persona = "online"
    entry = (str(persona), clean_activity(data.get("activity")))
    if _remote.get(node_id) == entry:
        return False
    _remote[node_id] = entry
    return True


def forget_remote(node_id: str) -> None:
    _remote.pop(node_id, None)


def for_devices(online_node_ids: list[str]) -> tuple[SharedPersona, Activity | None]:
    """A person's persona and activity, from whichever of their machines are up.

    A connected machine that has not announced anything — an older build, or the
    instant before its first `social_status` lands — counts as `online`, which is
    exactly what it would have shown before personas existed.
    """
    if not online_node_ids:
        return "offline", None
    best: SharedPersona = "offline"
    activity: Activity | None = None
    for node_id in online_node_ids:
        persona, act = _remote.get(node_id, ("online", None))
        if _RANK[persona] > _RANK[best]:
            best = persona  # type: ignore[assignment]
        if persona != "offline" and act is not None:
            if activity is None or act.since > activity.since:
                activity = act
    return best, (activity if best != "offline" else None)


def reset_for_tests() -> None:
    global _activity, _idle
    _activity = None
    _idle = False
    _remote.clear()
