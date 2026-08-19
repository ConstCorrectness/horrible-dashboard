"""The bridge between the fabric roster and the game server's friend list.

There were two friend systems, and they did not know about each other:

- the **fabric roster** (`social_friends`, this module's neighbours) — keyed by
  `person_id`, stored locally, self-certifying, and the thing that grants peer
  trust. It has no profile data at all.
- the **ladder** (`friendships` + `player_profiles` on the game server) — keyed by
  `account_id`, stored centrally, with avatars, bios, XP and levels. It grants
  nothing.

The same human occupied a row in each with no way to tell. `accounts.person_id`
(bound by `handles.publish_binding`) was already the join; nothing was built on it.
This module builds on it, in both directions:

- `reconcile()` — fabric → ladder. Ask the directory which of my roster's people
  have accounts, and cache the answer on the row.
- `mirror_accept()` — when a fabric friendship is accepted, open the matching
  ladder friendship so one action produces one friend, not half of two.

**What this deliberately does not do is grant trust from a ladder friendship.**

That asymmetry is the whole security argument and it is easy to erode by accident.
A friend code is *self-certifying*: the person id is the fingerprint of the key, so
a friendship proven over the fabric is proven against the key itself. A handle is a
*claim the game server vouches for*. Wiring "ladder friend" → "trusted peer" would
mean a compromised or hostile game server could hand itself, or anyone, the trust
that lets a remote agent reach yours and shared panes flow.

So the fabric side keeps its own gate exactly as before: trust is granted in
`roster.respond` / `roster.handle_friend_response`, after a device certificate has
verified. What this module adds is the *other* half — the profile, the username, the
ladder friendship — none of which is an authority over anything.

The direction that is safe is the one implemented: fabric acceptance opens a ladder
friendship. A ladder friendship arriving on its own is surfaced as a suggestion, not
auto-accepted onto the fabric. See docs/modules/social.mdx.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.modules.games import server_auth
from backend.modules.social import identity as person_identity
from backend.modules.social import store

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0


def _base() -> str:
    """The game server's HTTP base — resolved the same way sign-in and play resolve
    it, so a node can't reconcile against one server and play against another."""
    from backend.modules.games.client import resolve_server_url

    url = resolve_server_url()
    return url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


async def resolve_people(person_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`person_id` → `{handle, display_name, account_id, person_public_key}`.

    Every entry is re-checked against the fingerprint invariant before it is
    returned, exactly as `handles.resolve` does for a single username: the person id
    must be the fingerprint of the key it arrived with. Without that check the
    directory could point a person id at a key that isn't theirs, and the cache
    would launder a server's claim into something the roster displays as fact.
    """
    ids = [p for p in person_ids if p]
    if not ids:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{_base()}/directory/people", json={"person_ids": ids}
            )
            data = res.json()
    except Exception as exc:  # noqa: BLE001 — reconciliation is advisory, never fatal
        logger.debug("ladder person lookup failed: %s", exc)
        return {}
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for person_id, entry in people.items():
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("person_public_key") or "")
        if not key or person_identity.fingerprint(key) != str(person_id):
            logger.warning(
                "directory entry for %s is inconsistent — ignoring", person_id
            )
            continue
        out[str(person_id)] = entry
    return out


async def reconcile() -> int:
    """Link roster rows that have no cached ladder identity yet. Returns how many.

    Run on startup and after a sign-in. Cheap and idempotent: rows already carrying
    an `account_id` are skipped, so the steady state is one request that asks about
    nothing. A person with no ladder account stays unlinked and is retried next
    time — which is correct, because they might sign up tomorrow.
    """
    pending = store.friends_missing_ladder_identity()
    if not pending:
        return 0
    found = await resolve_people([r["person_id"] for r in pending])
    for person_id, entry in found.items():
        store.set_ladder_identity(
            person_id,
            handle=str(entry.get("handle") or "") or None,
            account_id=str(entry.get("account_id") or "") or None,
        )
    if found:
        logger.info("linked %d roster row(s) to ladder accounts", len(found))
    return len(found)


async def mirror_accept(person_id: str) -> None:
    """A fabric friendship was just accepted — open the ladder one to match.

    Best-effort by design. The fabric friendship is the real one: it is what carries
    trust and what works on a LAN with no internet, and it has already succeeded by
    the time this runs. If the game server is down, or either side has no account,
    the friendship simply has no ladder half yet and `reconcile` picks it up later.
    Raising here would turn "your friend list has no avatar" into "adding a friend
    failed", which is a worse answer to a smaller problem.
    """
    if server_auth.signed_in_account() is None:
        return  # no ladder identity of our own to friend them from
    row = store.get_friend_row(person_id)
    if row is None:
        return
    account_id = row.get("account_id")
    if not account_id:
        # First contact — we may simply never have looked them up.
        found = await resolve_people([person_id])
        entry = found.get(person_id)
        if entry is None:
            return
        account_id = str(entry.get("account_id") or "") or None
        store.set_ladder_identity(
            person_id,
            handle=str(entry.get("handle") or "") or None,
            account_id=account_id,
        )
    if not account_id:
        return
    try:
        from backend.modules.games.client import games_client

        await games_client.friend_action("request", str(account_id))
        logger.info("opened ladder friendship with %s", account_id)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        logger.debug("ladder mirror for %s failed: %s", person_id, exc)
