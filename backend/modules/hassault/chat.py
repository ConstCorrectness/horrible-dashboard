"""In-match text chat: what a message may contain, and how it is stamped.

Chat is the one thing in a match made of text a *person* typed, so it is the one
place Unicode has to be right rather than merely tolerated. Three rules, each of
which is a bug if skipped:

- **Limits are counted in grapheme clusters, and cut only between them.** A
  family emoji is seven code points and twenty-five UTF-8 bytes; a flag is two
  regional indicators; "é" can be two code points. Truncating by code points or
  bytes — which `text[:200]` did — splits those into garbage (a lone skin-tone
  swatch, half a flag rendered as a letter), so both the grapheme limit and the
  byte ceiling are applied at a cluster boundary. `regex`'s `\\X` is the
  Unicode segmentation algorithm (UAX #29), not a heuristic.
- **Invisible format characters are removed, except the ones emoji are made
  of.** Bidi overrides and isolates (U+202A–202E, U+2066–2069) let a message
  reorder how *the next thing on the line* displays — in a chat box, that is
  somebody else's name. Zero-width joiners, variation selectors and tag
  characters stay, because ZWJ sequences, text/emoji presentation and
  subdivision flags (England, Scotland) are built from exactly those.
- **The server's copy is the only copy.** Every message is stamped with an id and
  a server timestamp and echoed to the sender too, so a client shows what the
  room actually received — cleaned, trimmed and in the server's order — rather
  than what it typed. Clients that echoed locally showed their own message
  twice.

Text is NFC-normalised so the same word typed on two keyboards is the same
string, and runs of whitespace (newlines included — a chat line is one line)
collapse to one space.
"""

from __future__ import annotations

import time
import unicodedata
import uuid
from collections import deque
from typing import Any

import regex

#: Longest message, in user-perceived characters.
MAX_GRAPHEMES = 200
#: And in UTF-8 bytes, because a grapheme can be arbitrarily long (a base letter
#: followed by a hundred combining marks is one cluster, "Zalgo" text).
MAX_BYTES = 1024
#: Messages per window, per player — enough to talk, not enough to scroll the box.
RATE_COUNT = 5
RATE_WINDOW = 4.0

_GRAPHEME = regex.compile(r"\X")
_WHITESPACE = regex.compile(r"\s+")

#: Format (Cf) characters kept because emoji and scripts are made of them.
_KEEP_FORMAT = frozenset(
    {
        0x200C,  # ZERO WIDTH NON-JOINER — required in Persian, Hindi, …
        0x200D,  # ZERO WIDTH JOINER — every ZWJ emoji sequence
    }
)


def _keep(ch: str) -> bool:
    cp = ord(ch)
    cat = unicodedata.category(ch)
    if cat == "Cc":
        return False  # control characters, newlines included (collapsed first)
    if cat == "Cf":
        # Tag characters spell subdivision flags (🏴 + tags + cancel tag).
        return cp in _KEEP_FORMAT or 0xE0020 <= cp <= 0xE007F
    if cat in ("Cs", "Co", "Cn"):
        return False  # lone surrogates, private use, unassigned
    return True


def clean(text: Any) -> str:
    """Normalise and trim a message. Returns '' when nothing sendable is left."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _WHITESPACE.sub(" ", text)
    text = "".join(ch for ch in text if _keep(ch)).strip()
    if not text:
        return ""
    out: list[str] = []
    size = 0
    for cluster in _GRAPHEME.findall(text):
        width = len(cluster.encode("utf-8"))
        if len(out) >= MAX_GRAPHEMES or size + width > MAX_BYTES:
            break
        out.append(cluster)
        size += width
    return "".join(out).strip()


def grapheme_count(text: str) -> int:
    return len(_GRAPHEME.findall(text))


class RateLimiter:
    """A sliding window per player id. Deliberately forgetful: a player who left
    takes nothing with them worth keeping."""

    def __init__(self, count: int = RATE_COUNT, window: float = RATE_WINDOW) -> None:
        self.count = count
        self.window = window
        self._sent: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        sent = self._sent.setdefault(key, deque())
        while sent and now - sent[0] > self.window:
            sent.popleft()
        if len(sent) >= self.count:
            return False
        sent.append(now)
        return True


limiter = RateLimiter()


def message(
    *, sender_id: str, sender_name: str, team: int, is_team: bool, text: str
) -> dict[str, Any]:
    """The wire shape every client renders. `id` lets a client drop a duplicate;
    `ts` is the server's clock, the only one all recipients share."""
    return {
        "id": uuid.uuid4().hex[:12],
        "ts": round(time.time() * 1000),
        "senderId": sender_id,
        "senderName": sender_name,
        "team": team,
        "isTeam": is_team,
        "text": text,
    }


async def post(room: Any, player: Any, raw: Any, is_team: bool) -> str | None:
    """Clean, rate-limit and broadcast one message from `player` in `room`.

    Returns why it was refused, or None when it was sent. One entry point for
    every way a message arrives — a browser on this node, a friend's node over
    the fabric, the native client — so none of them can be the lax one.
    """
    from backend.modules.hassault.match import match_server

    text = clean(raw)
    if not text:
        return "empty"
    if not limiter.allow(f"{room.id}:{player.id}"):
        return "slow down"
    payload = message(
        sender_id=player.id,
        sender_name=player.name,
        team=player.team,
        is_team=is_team,
        text=text,
    )
    await match_server.broadcast_event(
        room, "chat", payload, team=player.team if is_team else None
    )
    return None
