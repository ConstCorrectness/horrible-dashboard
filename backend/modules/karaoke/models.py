"""Pydantic models for the `/api/karaoke` boundary.

Two vocabularies live here and they are deliberately distinct:

* a **song** is a durable row in the node's library — a file on disk plus its
  metadata. It exists after a download and survives every restart.
* a **queue entry** is an ephemeral seat in tonight's running order — a song id, a
  singer's name, and an entry id. The same song queued twice is two entries, so
  the entry id (not the song id) is what removals and reorders address.

Conflating them is the bug this split exists to prevent: keying the queue by song
id makes "add it again for the next singer" silently a no-op.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# What a download can be doing. `queued` is the state a request returns in — the
# work happens on the task queue, and progress arrives on the `karaoke` channel.
DownloadStatus = Literal["queued", "downloading", "ready", "failed"]


class SearchResult(BaseModel):
    """One YouTube hit. Not yet a song — nothing is on disk until it's downloaded."""

    video_id: str
    title: str
    url: str
    channel: str = ""
    duration: int | None = None
    thumbnail: str | None = None
    # True when this video_id is already in the library, so the UI offers "queue"
    # rather than "download". Computed per-request against the songs table.
    downloaded: bool = False


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    # Why a search came back empty, when it did. An empty list with no note means
    # "the search ran and found nothing", which is a different thing from
    # "yt-dlp isn't installed" — the UI shows the difference.
    note: str = ""


class SongModel(BaseModel):
    id: str
    title: str
    artist: str = ""
    video_id: str = ""
    url: str = ""
    # Relative to the karaoke songs dir. Never handed to a caller as an absolute
    # path: playback goes through `/api/karaoke/media/{id}`, which is the only
    # thing that resolves it.
    filename: str = ""
    duration: float | None = None
    status: DownloadStatus = "ready"
    error: str | None = None
    size_bytes: int | None = None
    play_count: int = 0
    last_played: str | None = None
    added_at: str | None = None


class SongsResponse(BaseModel):
    songs: list[SongModel]


class DownloadRequest(BaseModel):
    """Fetch a video into the library. `url` or `video_id` — either identifies it."""

    url: str | None = None
    video_id: str | None = None
    title: str | None = None
    artist: str | None = None
    # Queue it for `singer` the moment it finishes. The whole point of a party
    # remote: you search, tap once, and your name is in the running order.
    queue_for: str | None = None


class QueueEntry(BaseModel):
    entry_id: str
    song_id: str
    title: str
    artist: str = ""
    singer: str = ""
    duration: float | None = None
    # Set once the entry has been played, so the history view can tell a finished
    # entry from one still waiting. Entries are removed on play, so this only ever
    # appears on the `history` list.
    played_at: str | None = None


class AddToQueueRequest(BaseModel):
    song_id: str
    singer: str = ""
    # Jump the line. The host's override — the mobile remote never sets it.
    next: bool = False


class MoveRequest(BaseModel):
    entry_id: str
    # Target index in the queue, clamped into range by the session.
    position: int


class PlayerState(BaseModel):
    """The whole shared session, in one object.

    Every client renders from this and nothing else — the stage, the queue pane and
    a phone across the room are all the same view over one server-held state. That
    is why `position`/`playing` live here rather than in the stage component: a
    remote has to be able to pause a video it isn't rendering.
    """

    now_playing: QueueEntry | None = None
    playing: bool = False
    # Seconds into the current song, as last reported by the stage. Advisory:
    # only the pane with the <video> element knows the true position, so this is
    # a display value for remotes, never the seek authority.
    position: float = 0.0
    duration: float | None = None
    volume: float = 1.0
    # Pitch shift in semitones, -6..+6. Applied by re-encoding the audio on the
    # media endpoint — see `transpose.py`.
    semitones: int = 0
    queue: list[QueueEntry] = Field(default_factory=list)
    history: list[QueueEntry] = Field(default_factory=list)
    # Auto-advance to the next entry when a song ends.
    autoplay: bool = True
    # Bumped on every mutation. A client that receives a stale broadcast (out of
    # order, or after a reconnect replay) drops it instead of rendering backwards.
    revision: int = 0


class VolumeRequest(BaseModel):
    volume: float = Field(ge=0.0, le=1.0)


class TransposeRequest(BaseModel):
    # ±6 semitones is the useful range; past that the re-encode sounds like a
    # chipmunk and nobody can sing to it.
    semitones: int = Field(ge=-6, le=6)


class SeekRequest(BaseModel):
    position: float = Field(ge=0.0)


class ProgressRequest(BaseModel):
    """The stage reporting where it actually is, so remotes can show a scrubber."""

    position: float = Field(ge=0.0)
    duration: float | None = None


class AutoplayRequest(BaseModel):
    autoplay: bool


class OkResponse(BaseModel):
    ok: bool = True
