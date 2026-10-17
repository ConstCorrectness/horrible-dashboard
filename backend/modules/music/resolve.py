"""Turn a *described* track (artist, title, duration — from a playlist export) into
something a source can fetch, and say how sure we are.

The failure this guards against is silent: a Spotify playlist of studio recordings
comes back as a library full of live cuts, karaoke versions and nightcore edits,
because the first YouTube hit for "Africa Toto" is not always the song. So every
candidate is scored, and a match below `music.minMatchConfidence` is **not
downloaded** — it lands in the review list with its alternatives, for a person to
pick.

Scoring is deliberately simple and explainable (it shows up in the review UI):
title tokens present, artist present, duration within a few seconds, a bonus for
official channels, and a penalty for version words the description didn't ask for.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from backend.modules.music import library
from backend.modules.music.providers import DescribedTrack, TrackCandidate, sources

logger = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.6

# Version words: a candidate carrying one the description doesn't is probably a
# different recording. Weighted by how badly a wrong one ruins a listen.
_VERSION_PENALTIES: dict[str, float] = {
    "karaoke": 0.25,
    "nightcore": 0.25,
    "sped up": 0.3,
    "slowed": 0.3,
    "8d": 0.35,
    "reverb": 0.5,
    "instrumental": 0.35,
    "cover": 0.4,
    "live": 0.5,
    "remix": 0.5,
    "acoustic": 0.6,
    "piano": 0.6,
    "reaction": 0.2,
    "tutorial": 0.2,
    "lesson": 0.2,
}
_WORD = re.compile(r"[\w']+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD.findall(library.normalize(text))


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", haystack) is not None


@dataclass
class Scored:
    candidate: TrackCandidate
    score: float
    why: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        c = self.candidate
        return {
            "source": c.source,
            "ref": c.ref,
            "title": c.title,
            "artist": c.artist,
            "channel": c.channel,
            "duration": c.duration,
            "url": c.url,
            "art_url": c.art_url,
            "score": round(self.score, 3),
            "why": self.why,
        }


def score(desc: DescribedTrack, cand: TrackCandidate) -> Scored:
    raw_title = str(cand.raw.get("title") or "") or f"{cand.artist} - {cand.title}"
    haystack = library.normalize(
        f"{raw_title} {cand.title} {cand.artist} {cand.channel}"
    )
    hay_tokens = set(_tokens(haystack))
    why: list[str] = []

    want = _tokens(desc.title)
    title_part = sum(1 for t in want if t in hay_tokens) / len(want) if want else 0.0
    why.append(f"title {title_part:.0%}")

    artist_tokens = _tokens(desc.artist)
    if artist_tokens:
        artist_part = sum(1 for t in artist_tokens if t in hay_tokens) / len(
            artist_tokens
        )
        why.append(f"artist {artist_part:.0%}")
    else:
        artist_part = 0.5

    if desc.duration and cand.duration:
        diff = abs(desc.duration - cand.duration)
        duration_part = (
            1.0 if diff <= 2 else 0.8 if diff <= 5 else 0.4 if diff <= 10 else 0.0
        )
        why.append(f"duration Δ{diff:.0f}s")
    else:
        duration_part = 0.5

    total = 0.45 * title_part + 0.25 * artist_part + 0.30 * duration_part

    channel = cand.channel.lower()
    if (
        channel.endswith(" - topic")
        or "vevo" in channel
        or (
            artist_tokens
            and library.normalize(cand.channel) == library.normalize(desc.artist)
        )
    ):
        total += 0.1
        why.append("official channel")

    asked = library.normalize(f"{desc.title} {desc.album}")
    for word, factor in _VERSION_PENALTIES.items():
        if _contains_phrase(haystack, word) and not _contains_phrase(asked, word):
            total *= factor
            why.append(f"'{word}' version")

    return Scored(candidate=cand, score=max(0.0, min(1.0, total)), why=why)


@dataclass
class Resolution:
    best: Scored | None
    alternatives: list[Scored]
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return self.best.score if self.best else 0.0


def query_for(desc: DescribedTrack) -> str:
    title = desc.title.strip()
    return f"{desc.artist.strip()} - {title}" if desc.artist.strip() else title


async def resolve(
    desc: DescribedTrack, *, source_ids: list[str] | None = None, limit: int = 6
) -> Resolution:
    """Search the sources for `desc` and rank what comes back."""
    chosen, notes = sources.resolve(source_ids)
    scored: list[Scored] = []
    for source in chosen:
        try:
            found = await source.search(query_for(desc), limit=limit)
        except Exception as exc:  # noqa: BLE001 — one source failing is a note
            notes.append(f"{source.id}: {exc}")
            continue
        scored += [score(desc, c) for c in found]
    scored.sort(key=lambda s: s.score, reverse=True)
    return Resolution(
        best=scored[0] if scored else None, alternatives=scored[:5], notes=notes
    )


def min_confidence() -> float:
    try:
        from backend.modules.settings.routes import get_value  # noqa: PLC0415

        return float(get_value("music.minMatchConfidence", DEFAULT_MIN_CONFIDENCE))
    except Exception:  # noqa: BLE001
        return DEFAULT_MIN_CONFIDENCE


def match_library(desc: DescribedTrack) -> dict[str, Any] | None:
    """A library track that already *is* this description: by a stable id first
    (re-syncing a Spotify playlist finds every old track this way), then by name."""
    for kind in ("spotify", "isrc", "mbid", "apple", "youtube"):
        value = desc.external_ids.get(kind)
        if value:
            hit = library.find_by_external_id(kind, value)
            if hit is not None:
                return hit
    return library.find_by_name(desc.artist, desc.title)
