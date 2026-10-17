"""The three extension points of the music library, and their registries.

The library is the app's own: every track is a file under the data dir with a row
in `music_tracks`, and the player only ever plays those. Outside services *feed*
it, through three contracts:

- a **source** finds music and fetches it into the library (YouTube via yt-dlp is
  the built-in; a plugin could add SoundCloud, a Subsonic server, a NAS share);
- an **importer** describes playlists — from an account (Spotify) or from a file
  someone exported (Apple Music, M3U, CSV). It never downloads anything itself: an
  import job resolves each described track against the library, then a source;
- a **listener** is told what played (Last.fm and ListenBrainz scrobbling) and can
  answer "what's similar to this?", which is what the DJ and radio are built on.

All three are `Protocol`s rather than ABCs, matching `search/base.py`: a backend
plugin contributes one (`host.add_music_source` etc.) without importing a base
class from here. The agent reaches every registered provider through the existing
`music.*` tools — a new source is one file and zero new tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)


# --- shapes ---------------------------------------------------------------------


@dataclass(frozen=True)
class TrackCandidate:
    """Something a source could fetch. Not in the library yet."""

    source: str
    # The source's own id for it (a YouTube video id). Stable across searches.
    ref: str
    title: str
    artist: str = ""
    album: str = ""
    duration: float | None = None
    url: str = ""
    art_url: str | None = None
    # Who published it (a YouTube channel). The resolver uses it: "Toto - Topic"
    # and "TotoVEVO" are the official uploads.
    channel: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class FetchedFile:
    """What a source hands back once the file is on disk."""

    path: Path
    title: str = ""
    artist: str = ""
    duration: float | None = None


@dataclass(frozen=True)
class DescribedTrack:
    """A track as an importer or a listener describes it: metadata, no file.

    `external_ids` carries whatever stable ids the describer has (`spotify`,
    `isrc`, `mbid`, `apple`); re-syncing a playlist matches on them first.
    """

    title: str
    artist: str = ""
    album: str = ""
    duration: float | None = None
    external_ids: dict[str, str] = field(default_factory=dict, compare=False)
    art_url: str = field(default="", compare=False)

    @property
    def isrc(self) -> str | None:
        return self.external_ids.get("isrc")


@dataclass(frozen=True)
class PlaylistSummary:
    ref: str
    name: str
    track_count: int | None = None
    owner: str = ""
    # Why this playlist can't be fully imported, when it can't (Spotify withholds
    # the items of playlists the user doesn't own).
    note: str = ""


@dataclass
class ImportedPlaylist:
    name: str
    # Where it came from, as stored on the local playlist: `spotify:<id>`,
    # `apple-file`, `m3u`, `lastfm:loved`. Re-sync is keyed on it.
    origin: str
    tracks: list[DescribedTrack] = field(default_factory=list)
    note: str = ""


# --- contracts ------------------------------------------------------------------


@runtime_checkable
class MusicSource(Protocol):
    id: str
    label: str

    def configured(self) -> bool:
        """Can this source run right now? Must not raise or touch the network."""
        ...

    async def search(self, query: str, *, limit: int = 10) -> list[TrackCandidate]:
        """Ranked candidates, best first. Raises `ProviderError` on failure."""
        ...

    async def fetch(
        self, candidate: TrackCandidate, target: Path, file_id: str
    ) -> FetchedFile:
        """Download `candidate` into `target` as `<file_id>.<ext>`. Raises on failure."""
        ...


@runtime_checkable
class PlaylistImporter(Protocol):
    id: str
    label: str
    # `account`: lists and reads playlists from a connected service.
    # `file`: parses an export the user uploads.
    kind: Literal["account", "file"]

    def configured(self) -> bool: ...

    async def list_playlists(self) -> list[PlaylistSummary]:
        """Account importers only."""
        ...

    async def read_playlist(self, ref: str) -> ImportedPlaylist:
        """Account importers only."""
        ...

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        """File importers only. Raises `ProviderError` on a file it can't read."""
        ...


@runtime_checkable
class MusicListener(Protocol):
    id: str
    label: str

    def configured(self) -> bool: ...

    async def now_playing(self, track: DescribedTrack) -> None: ...

    async def scrobble(self, track: DescribedTrack, started_at: int) -> None:
        """Record a finished listen. Raises `ProviderError` so the caller can
        queue it for a retry."""
        ...

    async def similar(
        self, seed: DescribedTrack, limit: int = 20
    ) -> list[DescribedTrack]:
        """Tracks like `seed`, best first. `[]` when the listener has no opinion."""
        ...


class ProviderError(RuntimeError):
    """A provider failed for this call. Callers degrade rather than crash."""


# --- registries -----------------------------------------------------------------

P = TypeVar("P")


class Registry(Generic[P]):
    """Process-global, later registrations win (a plugin can override a built-in by
    reusing its id). Same shape as `search/base.py`'s provider registry."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, P] = {}

    def register(self, provider: P) -> None:
        self._items[provider.id] = provider  # type: ignore[attr-defined]

    def unregister(self, provider_id: str) -> None:
        self._items.pop(provider_id, None)

    def get(self, provider_id: str) -> P | None:
        return self._items.get(provider_id)

    def all(self) -> list[P]:
        return list(self._items.values())

    def available(self) -> list[P]:
        out: list[P] = []
        for provider in self._items.values():
            try:
                if provider.configured():  # type: ignore[attr-defined]
                    out.append(provider)
            except Exception:  # noqa: BLE001 — a broken `configured` must not break the rest
                logger.exception("%s %s configured() failed", self._kind, provider)
        return out

    def resolve(self, names: list[str] | None = None) -> tuple[list[P], list[str]]:
        """The providers to use, plus human-readable notes about the rest — which
        ride out in tool results so the model can say "Last.fm isn't connected"
        instead of concluding nothing exists."""
        notes: list[str] = []
        if names:
            chosen: list[P] = []
            for name in names:
                provider = self._items.get(name)
                if provider is None:
                    notes.append(f"{name}: unknown {self._kind}")
                elif not provider.configured():  # type: ignore[attr-defined]
                    notes.append(f"{name}: not connected")
                else:
                    chosen.append(provider)
            return chosen, notes
        ready = self.available()
        ready_ids = {p.id for p in ready}  # type: ignore[attr-defined]
        for provider in self._items.values():
            if provider.id not in ready_ids:  # type: ignore[attr-defined]
                notes.append(f"{provider.id}: not connected")  # type: ignore[attr-defined]
        return ready, notes


sources: Registry[MusicSource] = Registry("source")
importers: Registry[PlaylistImporter] = Registry("importer")
listeners: Registry[MusicListener] = Registry("listener")
