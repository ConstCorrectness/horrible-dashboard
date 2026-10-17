"""Pydantic models for the `/api/music` boundary.

Same split as karaoke's: a **track** is a durable row in the library (a file plus
metadata); a **queue entry** is a seat in the play order, addressed by entry id so
the same track queued twice is two entries.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TrackStatus = Literal["queued", "downloading", "ready", "failed", "needs_review"]
RepeatMode = Literal["off", "one", "all"]


class TrackModel(BaseModel):
    id: str
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_no: int | None = None
    disc_no: int | None = None
    year: str = ""
    genre: str = ""
    duration: float | None = None
    bitrate: float | None = None
    size_bytes: int | None = None
    status: TrackStatus = "ready"
    error: str | None = None
    source: str = ""
    source_ref: str = ""
    external_ids: dict[str, str] = Field(default_factory=dict)
    art_hash: str = ""
    match_confidence: float | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=list)
    play_count: int = 0
    skip_count: int = 0
    last_played: str | None = None
    rating: int | None = None
    added_at: str | None = None


class TracksResponse(BaseModel):
    tracks: list[TrackModel]
    total: int


class CandidateModel(BaseModel):
    source: str
    ref: str
    title: str
    artist: str = ""
    album: str = ""
    duration: float | None = None
    url: str = ""
    art_url: str | None = None
    channel: str = ""
    # Already in the library — the UI offers "play" instead of "download".
    track_id: str | None = None


class SearchResponse(BaseModel):
    query: str
    candidates: list[CandidateModel] = Field(default_factory=list)
    # Why a search came back empty, and which sources were skipped.
    notes: list[str] = Field(default_factory=list)


class DownloadRequest(BaseModel):
    """Fetch something into the library. `url`, or a candidate from /search."""

    source: str = "youtube"
    ref: str | None = None
    url: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    # play | next | queue | none — what to do with it once it's a row.
    then: Literal["play", "next", "queue", "none"] = "none"


class ImportFilesRequest(BaseModel):
    # Absolute paths on this node: files, or folders to walk.
    paths: list[str]
    mode: Literal["copy", "move"] = "copy"


class ImportFilesResponse(BaseModel):
    imported: list[TrackModel] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class EditTrackRequest(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    track_no: int | None = None
    disc_no: int | None = None
    year: str | None = None
    genre: str | None = None
    rating: int | None = Field(default=None, ge=0, le=5)


class QueueEntry(BaseModel):
    entry_id: str
    track_id: str
    title: str
    artist: str = ""
    album: str = ""
    duration: float | None = None
    art_hash: str = ""
    # The file isn't on disk yet (queued mid-download). The player waits on it.
    ready: bool = True
    # Why it's in the queue, when something other than a person put it there:
    # `radio`, `dj`, `playlist:<name>`.
    reason: str = ""
    played_at: str | None = None


class PlayerState(BaseModel):
    now_playing: QueueEntry | None = None
    playing: bool = False
    # Advisory, as last reported by the client holding the <audio> element.
    position: float = 0.0
    duration: float | None = None
    volume: float = 0.8
    shuffle: bool = False
    repeat: RepeatMode = "off"
    # Refill the queue with similar music when it runs dry.
    radio: bool = False
    queue: list[QueueEntry] = Field(default_factory=list)
    history: list[QueueEntry] = Field(default_factory=list)
    # Which client (browser window) owns the audio. Exactly one plays: with the app
    # open in two windows, both would otherwise play the same song a beat apart.
    output_client: str | None = None
    revision: int = 0


class QueueTracksRequest(BaseModel):
    track_ids: list[str]
    mode: Literal["play", "next", "queue", "replace"] = "queue"
    reason: str = ""


class QueuePlaylistRequest(BaseModel):
    playlist_id: str
    mode: Literal["play", "next", "queue", "replace"] = "replace"
    shuffle: bool = False


class MoveRequest(BaseModel):
    entry_id: str
    position: int


class SeekRequest(BaseModel):
    position: float = Field(ge=0.0)


class VolumeRequest(BaseModel):
    volume: float = Field(ge=0.0, le=1.0)


class FlagRequest(BaseModel):
    value: bool


class RepeatRequest(BaseModel):
    mode: RepeatMode


class ProgressRequest(BaseModel):
    position: float = Field(ge=0.0)
    duration: float | None = None
    client_id: str | None = None


class ClaimRequest(BaseModel):
    client_id: str
    # A person pressed play *here*: take the output even from a live owner.
    force: bool = False


class EndedRequest(BaseModel):
    client_id: str | None = None


class PlaylistModel(BaseModel):
    id: str
    name: str
    origin: str = "local"
    description: str = ""
    track_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    tracks: list[TrackModel] | None = None


class CreatePlaylistRequest(BaseModel):
    name: str
    track_ids: list[str] = Field(default_factory=list)
    from_queue: bool = False


class UpdatePlaylistRequest(BaseModel):
    name: str | None = None
    # Replaces the whole order when given.
    track_ids: list[str] | None = None


class PlaylistTracksRequest(BaseModel):
    track_ids: list[str]


class OkResponse(BaseModel):
    ok: bool = True
