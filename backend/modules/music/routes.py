"""HTTP surface for the music library and player (`/api/music/*`).

Two halves, like karaoke's: the **library** (`/tracks`, `/search`, `/download`,
`/import`, `/playlists`, `/lyrics`) is durable, and the **player** (`/player`,
`/queue`) is the shared session. Every session mutation returns the whole
`PlayerState` and broadcasts it, so the caller gets its answer synchronously while
every other client (the shell indicator, the board widget) updates from the same
write.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from backend.modules import ytdl
from backend.modules.music import acquire, curator, enrich, events, jobs, library, lyrics
from backend.modules.music import listeners as music_listeners
from backend.modules.music.models import (
    CandidateModel,
    ClaimRequest,
    EndedRequest,
    CreatePlaylistRequest,
    DownloadRequest,
    EditTrackRequest,
    FlagRequest,
    ImportFilesRequest,
    ImportFilesResponse,
    MoveRequest,
    OkResponse,
    PlayerState,
    PlaylistModel,
    PlaylistTracksRequest,
    ProgressRequest,
    QueuePlaylistRequest,
    QueueTracksRequest,
    RepeatRequest,
    SearchResponse,
    SeekRequest,
    TrackModel,
    TracksResponse,
    UpdatePlaylistRequest,
    VolumeRequest,
)
from backend.modules.music.providers import (
    ProviderError,
    TrackCandidate,
    importers,
    listeners,
    sources,
)
from backend.modules.music.session import session
from backend.modules.ranged_file import ranged_file_response

router = APIRouter(prefix="/music", tags=["music"])


# --- library ---------------------------------------------------------------------


@router.get("/tracks", response_model=TracksResponse)
async def list_tracks(
    search: str = "",
    artist: str = "",
    album: str = "",
    status: str | None = None,
    sort: str = "added",
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> TracksResponse:
    rows = library.list_tracks(
        search,
        artist=artist,
        album=album,
        status=status,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return TracksResponse(
        tracks=[TrackModel(**r) for r in rows], total=library.count_tracks(None)
    )


@router.get("/tracks/{track_id}", response_model=TrackModel)
async def get_track(track_id: str) -> TrackModel:
    track = library.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    return TrackModel(**track)


@router.patch("/tracks/{track_id}", response_model=TrackModel)
async def edit_track(track_id: str, req: EditTrackRequest) -> TrackModel:
    track = library.edit_track(track_id, **req.model_dump(exclude_none=True))
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    await events.publish_track(track)
    await session.track_changed(track)
    return TrackModel(**track)


@router.delete("/tracks/{track_id}", response_model=OkResponse)
async def delete_track(track_id: str) -> OkResponse:
    if not library.delete_track(track_id):
        raise HTTPException(status_code=404, detail="track not found")
    await events.publish("library", {"removed": [track_id]})
    await session.track_removed(track_id)
    return OkResponse()


@router.get("/artists")
async def list_artists() -> dict[str, Any]:
    return {"artists": library.list_artists()}


@router.get("/albums")
async def list_albums(artist: str = "") -> dict[str, Any]:
    return {"albums": library.list_albums(artist)}


@router.get("/media/{track_id}")
async def media(track_id: str, request: Request) -> Response:
    track = library.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    path = library.track_path(track)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="track has no media file yet")
    return ranged_file_response(path, request, default_mime="audio/mp4")


@router.get("/art/{art_hash}")
async def art(art_hash: str, request: Request) -> Response:
    path = library.art_path(art_hash)
    if path is None:
        raise HTTPException(status_code=404, detail="no such art")
    response = ranged_file_response(path, request, default_mime="image/jpeg")
    # Content-addressed: the bytes behind a hash never change.
    response.headers["cache-control"] = "public, max-age=31536000, immutable"
    return response


# --- acquiring ---------------------------------------------------------------------


def _candidate_model(
    cand: TrackCandidate, row: dict[str, Any] | None
) -> CandidateModel:
    return CandidateModel(
        source=cand.source,
        ref=cand.ref,
        title=cand.title,
        artist=cand.artist,
        album=cand.album,
        duration=cand.duration,
        url=cand.url,
        art_url=cand.art_url,
        channel=cand.channel,
        track_id=row["id"] if row and row.get("status") != "failed" else None,
    )


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="What to look for"),
    limit: int = Query(10, ge=1, le=30),
    source: list[str] | None = Query(None),
) -> SearchResponse:
    pairs, notes = await acquire.search(q, limit=limit, source_ids=source)
    return SearchResponse(
        query=q, candidates=[_candidate_model(c, r) for c, r in pairs], notes=notes
    )


async def _queue_with(track: dict[str, Any], then: str) -> None:
    if then != "none":
        await session.add([track], mode=then)


@router.post("/download", response_model=TrackModel)
async def download(req: DownloadRequest) -> TrackModel:
    """Fetch into the library. Returns the row right away (`queued`, or `ready` if
    it was already there); progress arrives as `track` events."""
    ref = req.ref or ""
    if req.source == "youtube" and not ref:
        ref = ytdl.parse_video_id(req.url or "")
    if not ref and not req.url:
        raise HTTPException(status_code=400, detail="ref or url is required")
    existing = library.find_by_source(req.source, ref) if ref else None
    if existing is not None:
        await _queue_with(existing, req.then)
        return TrackModel(**existing)
    candidate = TrackCandidate(
        source=req.source,
        ref=ref or (req.url or ""),
        title=req.title or "",
        artist=req.artist or "",
        album=req.album or "",
        url=req.url or "",
    )
    try:
        track = await acquire.start_download(candidate)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await _queue_with(track, req.then)
    return TrackModel(**track)


@router.post("/import/files", response_model=ImportFilesResponse)
async def import_files(req: ImportFilesRequest) -> ImportFilesResponse:
    if not req.paths:
        raise HTTPException(status_code=400, detail="paths is required")
    result = await acquire.import_files(req.paths, mode=req.mode)
    return ImportFilesResponse(
        imported=[TrackModel(**r) for r in result["imported"]],
        skipped=result["skipped"],
        failed=result["failed"],
    )


# A single song, even a long lossless one, is well under this.
_MAX_AUDIO_UPLOAD = 1024 * 1024 * 1024


@router.post("/upload", response_model=ImportFilesResponse)
async def upload_files(files: list[UploadFile] = File(...)) -> ImportFilesResponse:
    """Audio dropped on the music pane: written to a staging folder inside the
    library, then imported (moved) exactly like a file on disk. The browser can't
    hand over a path, so this is how a web client adds its own files."""
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from backend.modules.music import tags  # noqa: PLC0415

    staging = Path(tempfile.mkdtemp(prefix="upload-", dir=library.music_dir()))
    try:
        paths: list[str] = []
        failed: list[str] = []
        for upload in files:
            name = Path(upload.filename or "upload").name
            if Path(name).suffix.lower() not in tags.AUDIO_EXTS:
                failed.append(f"{name}: not an audio file")
                continue
            target = staging / name
            size = 0
            with target.open("wb") as out:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > _MAX_AUDIO_UPLOAD:
                        break
                    out.write(chunk)
            if size > _MAX_AUDIO_UPLOAD:
                target.unlink(missing_ok=True)
                failed.append(f"{name}: too large")
                continue
            paths.append(str(target))
        result = await acquire.import_files(paths, mode="move") if paths else {"imported": [], "skipped": [], "failed": []}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return ImportFilesResponse(
        imported=[TrackModel(**r) for r in result["imported"]],
        skipped=[Path(p).name for p in result["skipped"]],
        failed=failed + [Path(p).name for p in result["failed"]],
    )


# --- lyrics ------------------------------------------------------------------------


@router.get("/lyrics/{track_id}")
async def get_lyrics(track_id: str, refresh: bool = False) -> dict[str, Any]:
    found = await lyrics.get_lyrics(track_id, refresh=refresh)
    if found is None:
        raise HTTPException(status_code=404, detail="track not found")
    return found


# --- playlists ---------------------------------------------------------------------


def _playlist_or_404(playlist_id: str, *, with_tracks: bool = False) -> dict[str, Any]:
    playlist = library.get_playlist(playlist_id, with_tracks=with_tracks)
    if playlist is None:
        raise HTTPException(status_code=404, detail="playlist not found")
    return playlist


@router.get("/playlists")
async def list_playlists() -> dict[str, Any]:
    return {
        "playlists": [PlaylistModel(**p).model_dump() for p in library.list_playlists()]
    }


@router.post("/playlists", response_model=PlaylistModel)
async def create_playlist(req: CreatePlaylistRequest) -> PlaylistModel:
    playlist = library.create_playlist(req.name)
    ids = list(req.track_ids)
    if req.from_queue:
        state = session.snapshot()
        ids += [
            e.track_id
            for e in ([state.now_playing] if state.now_playing else []) + state.queue
        ]
    if ids:
        library.add_to_playlist(playlist["id"], ids)
    await events.publish("library", {"playlists": True})
    return PlaylistModel(**_playlist_or_404(playlist["id"]))


@router.get("/playlists/{playlist_id}", response_model=PlaylistModel)
async def get_playlist(playlist_id: str) -> PlaylistModel:
    return PlaylistModel(**_playlist_or_404(playlist_id, with_tracks=True))


@router.patch("/playlists/{playlist_id}", response_model=PlaylistModel)
async def update_playlist(
    playlist_id: str, req: UpdatePlaylistRequest
) -> PlaylistModel:
    _playlist_or_404(playlist_id)
    if req.name is not None:
        library.rename_playlist(playlist_id, req.name)
    if req.track_ids is not None:
        known = library.get_tracks(req.track_ids)
        library.set_playlist_tracks(
            playlist_id, [t for t in req.track_ids if t in known]
        )
    await events.publish("library", {"playlists": True})
    return PlaylistModel(**_playlist_or_404(playlist_id, with_tracks=True))


@router.post("/playlists/{playlist_id}/tracks", response_model=PlaylistModel)
async def add_playlist_tracks(
    playlist_id: str, req: PlaylistTracksRequest
) -> PlaylistModel:
    _playlist_or_404(playlist_id)
    known = library.get_tracks(req.track_ids)
    library.add_to_playlist(playlist_id, [t for t in req.track_ids if t in known])
    await events.publish("library", {"playlists": True})
    return PlaylistModel(**_playlist_or_404(playlist_id, with_tracks=True))


@router.post("/playlists/{playlist_id}/tracks/remove", response_model=PlaylistModel)
async def remove_playlist_tracks(
    playlist_id: str, req: PlaylistTracksRequest
) -> PlaylistModel:
    _playlist_or_404(playlist_id)
    library.remove_from_playlist(playlist_id, req.track_ids)
    await events.publish("library", {"playlists": True})
    return PlaylistModel(**_playlist_or_404(playlist_id, with_tracks=True))


@router.delete("/playlists/{playlist_id}", response_model=OkResponse)
async def delete_playlist(playlist_id: str) -> OkResponse:
    if not library.delete_playlist(playlist_id):
        raise HTTPException(status_code=404, detail="playlist not found")
    await events.publish("library", {"playlists": True})
    return OkResponse()


# --- player ------------------------------------------------------------------------


@router.get("/player", response_model=PlayerState)
async def get_player() -> PlayerState:
    return session.snapshot()


@router.post("/queue", response_model=PlayerState)
async def queue_tracks(req: QueueTracksRequest) -> PlayerState:
    found = library.get_tracks(req.track_ids)
    tracks = [
        found[t] for t in req.track_ids if t in found and found[t]["status"] != "failed"
    ]
    if not tracks:
        raise HTTPException(status_code=404, detail="no playable tracks")
    await session.add(tracks, mode=req.mode, reason=req.reason)
    return session.snapshot()


@router.post("/queue/playlist", response_model=PlayerState)
async def queue_playlist(req: QueuePlaylistRequest) -> PlayerState:
    playlist = _playlist_or_404(req.playlist_id, with_tracks=True)
    tracks = [t for t in playlist.get("tracks", []) if t["status"] != "failed"]
    if not tracks:
        raise HTTPException(status_code=400, detail="playlist is empty")
    if req.shuffle:
        import random  # noqa: PLC0415

        random.shuffle(tracks)
    await session.add(tracks, mode=req.mode, reason=f"playlist:{playlist['name']}")
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


@router.post("/player/toggle", response_model=PlayerState)
async def toggle() -> PlayerState:
    await session.toggle()
    return session.snapshot()


@router.post("/player/next", response_model=PlayerState)
async def next_track() -> PlayerState:
    await session.next()
    return session.snapshot()


@router.post("/player/previous", response_model=PlayerState)
async def previous_track() -> PlayerState:
    await session.previous()
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


@router.post("/player/shuffle", response_model=PlayerState)
async def set_shuffle(req: FlagRequest) -> PlayerState:
    await session.set_shuffle(req.value)
    return session.snapshot()


@router.post("/player/repeat", response_model=PlayerState)
async def set_repeat(req: RepeatRequest) -> PlayerState:
    await session.set_repeat(req.mode)
    return session.snapshot()


@router.post("/player/radio", response_model=PlayerState)
async def set_radio(req: FlagRequest) -> PlayerState:
    await session.set_radio(req.value)
    return session.snapshot()


@router.post("/player/progress", response_model=OkResponse)
async def report_progress(req: ProgressRequest) -> OkResponse:
    """~1 Hz from the playing client. Returns `ok`, not the state: echoing the whole
    queue back every second would be pure waste."""
    await session.report_progress(req.position, req.duration, req.client_id)
    return OkResponse()


@router.post("/player/ended", response_model=PlayerState)
async def ended(req: EndedRequest | None = None) -> PlayerState:
    # Only the owner's "it ran out" advances: a second window whose copy of the
    # song ended would otherwise skip a track for everyone.
    if session.is_output(req.client_id if req else None):
        await session.ended()
    return session.snapshot()


@router.post("/player/claim", response_model=PlayerState)
async def claim_output(req: ClaimRequest) -> PlayerState:
    await session.claim_output(req.client_id, force=req.force)
    return session.snapshot()


# --- importers and jobs ------------------------------------------------------------


@router.get("/importers")
async def list_importers() -> dict[str, Any]:
    return {"importers": _provider_rows(importers)}


@router.get("/importers/{importer_id}/playlists")
async def importer_playlists(importer_id: str) -> dict[str, Any]:
    importer = importers.get(importer_id)
    if importer is None:
        raise HTTPException(status_code=404, detail="unknown importer")
    if importer.kind != "account":
        raise HTTPException(status_code=400, detail=f"{importer.label} imports a file")
    if not importer.configured():
        raise HTTPException(status_code=409, detail=f"{importer.label} isn't connected")
    try:
        found = await importer.list_playlists()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "playlists": [
            {"ref": p.ref, "name": p.name, "track_count": p.track_count, "owner": p.owner, "note": p.note}
            for p in found
        ]
    }


@router.post("/import/playlist")
async def import_playlist(body: dict[str, str]) -> dict[str, Any]:
    importer_id = (body.get("importer") or "").strip()
    ref = (body.get("ref") or "").strip()
    if not importer_id or not ref:
        raise HTTPException(status_code=400, detail="importer and ref are required")
    try:
        return await jobs.import_from_account(importer_id, ref)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# An exported playlist is text; anything this big is not one.
_MAX_UPLOAD = 20 * 1024 * 1024


@router.post("/import/file")
async def import_playlist_file(
    file: UploadFile = File(...), importer: str = Form("")
) -> dict[str, Any]:
    data = await file.read(_MAX_UPLOAD + 1)
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="that file is too large to be a playlist")
    filename = file.filename or "playlist"
    importer_id = importer or jobs.detect_file_importer(filename, data)
    try:
        return await jobs.import_from_file(importer_id, filename, data)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/playlists/{playlist_id}/sync")
async def sync_playlist(playlist_id: str) -> dict[str, Any]:
    try:
        return await jobs.resync(playlist_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    return {"jobs": jobs.list_jobs()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get_job(job_id, with_items=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=OkResponse)
async def cancel_job(job_id: str) -> OkResponse:
    if not jobs.cancel_job(job_id):
        raise HTTPException(status_code=404, detail="no running job with that id")
    return OkResponse()


@router.get("/review")
async def review_list() -> dict[str, Any]:
    return {"tracks": [TrackModel(**t).model_dump() for t in jobs.review_list()]}


@router.post("/review/{track_id}")
async def review_track(track_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Settle a low-confidence import match: `{accept: true, candidate?}` downloads
    it (the best candidate unless one is given), `{accept: false}` drops it."""
    try:
        track = await jobs.review(
            track_id, accept=bool(body.get("accept", True)), candidate=body.get("candidate")
        )
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if track is not None:
        await events.publish_track(track)
    return {"track": TrackModel(**track).model_dump() if track else None}


# --- enrichment and the DJ -----------------------------------------------------------


@router.post("/tracks/{track_id}/enrich", response_model=TrackModel)
async def enrich_track(track_id: str) -> TrackModel:
    track = await enrich.enrich_track(track_id, force=True)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    return TrackModel(**track)


@router.post("/dj", response_model=PlayerState)
async def dj(body: dict[str, Any]) -> PlayerState:
    """"More like this": queue `count` picks seeded from what's been playing (or
    `seeds`, a list of "Artist - Title" / artist strings)."""
    seeds = [str(s) for s in body.get("seeds") or [] if str(s).strip()]
    result = await curator.curate(
        seed_queries=seeds or None,
        count=int(body.get("count") or 10),
        allow_download=bool(body.get("allow_download", True)),
    )
    if result["picks"]:
        await session.add(result["picks"], mode="queue", reason="dj")
    return session.snapshot()


# --- status ------------------------------------------------------------------------


def _provider_rows(registry: Any) -> list[dict[str, Any]]:
    rows = []
    for provider in registry.all():
        try:
            ok = bool(provider.configured())
        except Exception:  # noqa: BLE001
            ok = False
        rows.append(
            {
                "id": provider.id,
                "label": provider.label,
                "configured": ok,
                **({"kind": provider.kind} if hasattr(provider, "kind") else {}),
            }
        )
    return rows


@router.get("/status")
async def status() -> dict[str, Any]:
    """What this node can do — the pane renders its capability notes from this."""
    return {
        "ytdlp": ytdl.available(),
        "track_count": library.count_tracks("ready"),
        "files_dir": str(library.files_dir()),
        "sources": _provider_rows(sources),
        "importers": _provider_rows(importers),
        "listeners": _provider_rows(listeners),
        "scrobbles_pending": music_listeners.pending_count(),
        "needs_review": library.count_tracks("needs_review"),
    }
