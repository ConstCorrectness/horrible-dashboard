"""Wallpapers: the one piece of the desktop shell that needs the server.

Everything else about a desktop — its mode, its backdrop id, its windows — rides
inside the workspace's opaque `layout` blob and needs no backend at all. A
wallpaper is different: it is a *file*, so it needs somewhere to live and a URL
to be served from.

No imagery ships with the app. These routes hold what the user supplied,
alongside the geoip database and the llama.cpp builds, under
`$HORRIBLE_DATA_DIR` — resolved through `backend.paths`, never an inline
`os.environ.get`, which would make the directory depend on whichever launcher
started the backend. See docs/architecture/data-directories.mdx.
"""

import secrets
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from backend import paths
from backend.modules.desktop.models import (
    WALLPAPER_ID_PATTERN,
    Wallpaper,
    WallpaperList,
)

router = APIRouter(prefix="/desktop", tags=["desktop"])

WallpaperId = Annotated[str, PathParam(pattern=WALLPAPER_ID_PATTERN)]

#: Only formats a browser can actually paint as a CSS background, mapped to the
#: extension we store. An allow-list rather than a deny-list, and the extension
#: comes from *this table* rather than from the uploaded filename — trusting the
#: client's extension is how an upload route starts writing `.html`.
ALLOWED_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/gif": ".gif",
}

#: 32 MiB. A desktop wallpaper far larger than this is a mistake rather than a
#: choice, and the file is read into memory to be written.
MAX_BYTES = 32 * 1024 * 1024


def _dir() -> Path:
    d = paths.data_dir() / "wallpapers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _describe(path: Path) -> Wallpaper | None:
    """A stored file as a `Wallpaper`, or None if it is not one of ours."""
    suffix = path.suffix.lower()
    content_type = next((t for t, ext in ALLOWED_TYPES.items() if ext == suffix), None)
    if content_type is None:
        return None
    stat = path.stat()
    return Wallpaper(
        id=path.stem,
        name=path.stem,
        content_type=content_type,
        size=stat.st_size,
        modified=stat.st_mtime,
        url=f"/desktop/wallpapers/{path.stem}",
    )


def _resolve(wallpaper_id: str) -> Path:
    """The stored file for `wallpaper_id`.

    The id is already pattern-checked to 32 hex characters at the route
    boundary, so it cannot contain a separator or a `..`. This *also* resolves
    the result and re-checks that it is inside the wallpaper directory: the
    pattern is the guard, and this is the assertion that the guard held, because
    a traversal here reads any file the backend can reach.
    """
    directory = _dir().resolve()
    for ext in ALLOWED_TYPES.values():
        candidate = (directory / f"{wallpaper_id}{ext}").resolve()
        if not candidate.is_relative_to(directory):
            raise HTTPException(status_code=400, detail="Invalid wallpaper id")
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="No such wallpaper")


@router.get("/wallpapers", response_model=WallpaperList)
def list_wallpapers() -> WallpaperList:
    items = [
        w for w in (_describe(p) for p in sorted(_dir().iterdir()) if p.is_file()) if w
    ]
    items.sort(key=lambda w: w.modified, reverse=True)
    return WallpaperList(wallpapers=items)


@router.post("/wallpapers", response_model=Wallpaper)
async def upload_wallpaper(file: Annotated[UploadFile, File()]) -> Wallpaper:
    ext = ALLOWED_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type. Allowed: {', '.join(sorted(ALLOWED_TYPES))}",
        )
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413, detail=f"Wallpaper exceeds {MAX_BYTES // 1024 // 1024} MiB"
        )
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    wallpaper_id = secrets.token_hex(16)
    target = _dir() / f"{wallpaper_id}{ext}"
    # `.part` + rename, the same rule the llama.cpp downloader follows: a
    # half-written file must never be visible to the listing route, or a
    # truncated image gets set as somebody's wallpaper.
    part = target.with_suffix(target.suffix + ".part")
    part.write_bytes(data)
    part.replace(target)

    return Wallpaper(
        id=wallpaper_id,
        name=Path(file.filename or "wallpaper").name,
        content_type=file.content_type or "application/octet-stream",
        size=len(data),
        modified=time.time(),
        url=f"/desktop/wallpapers/{wallpaper_id}",
    )


@router.get("/wallpapers/{wallpaper_id}")
def read_wallpaper(wallpaper_id: WallpaperId) -> FileResponse:
    path = _resolve(wallpaper_id)
    described = _describe(path)
    return FileResponse(
        path,
        media_type=described.content_type if described else "application/octet-stream",
        # Wallpapers are immutable — the id is minted per upload and a re-upload
        # gets a new one — so this can be cached hard. Without it the image is
        # re-fetched on every boot and every workspace switch.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.delete("/wallpapers/{wallpaper_id}")
def delete_wallpaper(wallpaper_id: WallpaperId) -> dict[str, bool]:
    _resolve(wallpaper_id).unlink()
    return {"deleted": True}
