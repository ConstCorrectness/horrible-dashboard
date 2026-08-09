"""HTTP surface for karaoke (`/api/karaoke/*`).

The shape mirrors the module's two halves: `/search` + `/songs` + `/download` are
the **library** (durable, per-song), and `/player` + `/queue` are the **session**
(shared, ephemeral). Every session mutation returns the whole `PlayerState`, and
also broadcasts it — so a caller gets its answer synchronously while every other
client in the room is updated by the same write.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from backend.modules.karaoke import downloader, store, transpose
from backend.modules.karaoke.models import (
    AddToQueueRequest,
    AutoplayRequest,
    DownloadRequest,
    MoveRequest,
    OkResponse,
    PlayerState,
    ProgressRequest,
    SearchResponse,
    SeekRequest,
    SongModel,
    SongsResponse,
    TransposeRequest,
    VolumeRequest,
)
from backend.modules.karaoke.session import session

router = APIRouter(prefix="/karaoke", tags=["karaoke"])

# Read this much per chunk when serving a byte range.
_STREAM_CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


# --- library ---


@router.get("/search", response_model=SearchResponse)
async def search_songs(
    q: str = Query(..., description="What to search YouTube for"),
    limit: int = Query(20, ge=1, le=50),
    karaoke: bool = Query(True, description="Bias the query toward karaoke versions"),
) -> SearchResponse:
    results, note = await downloader.search(q, limit=limit, karaoke_bias=karaoke)
    return SearchResponse(query=q, results=results, note=note)


@router.get("/songs", response_model=SongsResponse)
async def list_songs(
    search: str = "", limit: int = Query(500, ge=1, le=2000)
) -> SongsResponse:
    return SongsResponse(
        songs=[SongModel(**s) for s in store.list_songs(search, limit)]
    )


@router.post("/download", response_model=SongModel)
async def download(req: DownloadRequest) -> SongModel:
    """Start fetching a video. Returns immediately with a `queued`/`ready` row."""
    video_id = req.video_id or downloader.parse_video_id(req.url or "")
    if not video_id and not req.url:
        raise HTTPException(status_code=400, detail="url or video_id is required")

    # Already have it? Don't download it twice — just honour the queue request.
    # This is what makes tapping a search result idempotent for guests who don't
    # know (or care) what's already in the library.
    existing = store.find_by_video_id(video_id) if video_id else None
    if existing is not None:
        if req.queue_for is not None:
            await session.add(existing, singer=req.queue_for)
        return SongModel(**existing)

    if not downloader.available():
        raise HTTPException(status_code=503, detail=downloader.INSTALL_HINT)

    url = req.url or f"https://www.youtube.com/watch?v={video_id}"
    song = store.create_song(
        title=req.title or url,
        artist=req.artist or "",
        video_id=video_id,
        url=url,
        status="queued",
    )
    downloader.start_download(song["id"], url)
    if req.queue_for is not None:
        # Queued while still downloading, on purpose: the entry holds a place in
        # the running order and the file arrives long before the singer's turn.
        # Waiting for the download would mean a guest's song lands behind three
        # others they queued after.
        await session.add(song, singer=req.queue_for)
    return SongModel(**song)


@router.delete("/songs/{song_id}", response_model=OkResponse)
async def delete_song(song_id: str) -> OkResponse:
    if not store.delete_song(song_id):
        raise HTTPException(status_code=404, detail="song not found")
    return OkResponse()


def _resolve_media(song_id: str) -> tuple[dict[str, Any], Path]:
    song = store.get_song(song_id)
    if song is None:
        raise HTTPException(status_code=404, detail="song not found")
    path = store.song_path(song)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="song has no media file yet")
    return song, path


@router.get("/media/{song_id}")
async def media(song_id: str, request: Request, semitones: int = 0) -> Response:
    """Serve a song's video.

    At the original pitch this is a plain file response honouring Range requests,
    so the `<video>` element can seek. Transposed, it becomes a live ffmpeg
    transcode — unseekable by nature (see `transpose.py`), so it deliberately does
    *not* advertise `accept-ranges`, and a client that tries to seek gets a
    restart rather than a silently truncated stream.
    """
    song, path = _resolve_media(song_id)

    if semitones:
        if not -6 <= semitones <= 6:
            raise HTTPException(status_code=400, detail="semitones must be within ±6")
        if not transpose.available():
            raise HTTPException(
                status_code=503,
                detail="Pitch shifting needs ffmpeg on PATH.",
            )
        return StreamingResponse(
            transpose.stream(path, semitones),
            media_type="video/mp4",
            headers={"cache-control": "no-store"},
        )

    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=mime, headers={"accept-ranges": "bytes"})

    size = path.stat().st_size
    match = _RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="malformed Range header")
    raw_start, raw_end = match.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    else:
        # A suffix range ("bytes=-500") asks for the *last* N bytes. Treating the
        # number as a start offset is the classic misread and serves the wrong
        # part of the file.
        if not raw_end:
            raise HTTPException(status_code=416, detail="malformed Range header")
        start = max(0, size - int(raw_end))
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})

    def iter_range() -> Any:
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(_STREAM_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iter_range(),
        status_code=206,
        media_type=mime,
        headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(end - start + 1),
            "accept-ranges": "bytes",
        },
    )


# --- session ---


@router.get("/player", response_model=PlayerState)
async def get_player() -> PlayerState:
    return session.snapshot()


@router.post("/queue", response_model=PlayerState)
async def add_to_queue(req: AddToQueueRequest) -> PlayerState:
    song = store.get_song(req.song_id)
    if song is None:
        raise HTTPException(status_code=404, detail="song not found")
    await session.add(song, singer=req.singer, next_up=req.next)
    return session.snapshot()


@router.delete("/queue/{entry_id}", response_model=PlayerState)
async def remove_from_queue(entry_id: str) -> PlayerState:
    if not await session.remove(entry_id):
        raise HTTPException(status_code=404, detail="queue entry not found")
    return session.snapshot()


@router.post("/queue/move", response_model=PlayerState)
async def move_in_queue(req: MoveRequest) -> PlayerState:
    if not await session.move(req.entry_id, req.position):
        raise HTTPException(status_code=404, detail="queue entry not found")
    return session.snapshot()


@router.post("/queue/clear", response_model=PlayerState)
async def clear_queue() -> PlayerState:
    await session.clear()
    return session.snapshot()


@router.post("/player/play", response_model=PlayerState)
async def play() -> PlayerState:
    await session.set_playing(True)
    return session.snapshot()


@router.post("/player/pause", response_model=PlayerState)
async def pause() -> PlayerState:
    await session.set_playing(False)
    return session.snapshot()


@router.post("/player/next", response_model=PlayerState)
async def next_song() -> PlayerState:
    await session.next_song()
    return session.snapshot()


@router.post("/player/restart", response_model=PlayerState)
async def restart() -> PlayerState:
    await session.restart()
    return session.snapshot()


@router.post("/player/stop", response_model=PlayerState)
async def stop() -> PlayerState:
    await session.stop()
    return session.snapshot()


@router.post("/player/seek", response_model=PlayerState)
async def seek(req: SeekRequest) -> PlayerState:
    await session.seek(req.position)
    return session.snapshot()


@router.post("/player/volume", response_model=PlayerState)
async def set_volume(req: VolumeRequest) -> PlayerState:
    await session.set_volume(req.volume)
    return session.snapshot()


@router.post("/player/transpose", response_model=PlayerState)
async def set_transpose(req: TransposeRequest) -> PlayerState:
    await session.set_semitones(req.semitones)
    return session.snapshot()


@router.post("/player/autoplay", response_model=PlayerState)
async def set_autoplay(req: AutoplayRequest) -> PlayerState:
    await session.set_autoplay(req.autoplay)
    return session.snapshot()


@router.post("/player/progress", response_model=OkResponse)
async def report_progress(req: ProgressRequest) -> OkResponse:
    """The stage's position ping. Returns `ok` rather than the state on purpose —
    this fires once a second and echoing the whole queue back would be pure waste."""
    await session.report_progress(req.position, req.duration)
    return OkResponse()


@router.post("/player/ended", response_model=PlayerState)
async def song_ended() -> PlayerState:
    await session.song_ended()
    return session.snapshot()


@router.get("/status")
async def status() -> dict[str, Any]:
    """What this node can actually do — the pane renders its capability warnings
    from this rather than guessing."""
    return {
        "ytdlp": downloader.available(),
        "ffmpeg": transpose.available(),
        "songs_dir": str(store.songs_dir()),
        "song_count": len(store.list_songs(limit=2000)),
    }
