"""The native client, as a download from the game server.

`GET /download/hAssault.exe` is the Windows build of `apps/native-fps`, and
`GET /download/info` says whether there is one and what it is. A player fetches
the file, double-clicks it, and is in a match on this server: with no arguments
the client plays here as a guest, with no dashboard node involved
(`apps/native-fps/src/main.rs`, "Standalone").

### Why this server hosts it, and not only the GitHub release

A release already carries `hassault-native-win-x64.exe`, and a node installs it
from there (`backend/modules/hassault/client_install.py`). That asset is keyed by
a *tag*. This server is deployed from a checkout, by `pnpm deploy:games`, usually
some way ahead of the last tag — and the client and the server are two halves of
one wire. So the executable offered here is built by the same command, from the
same checkout, as the server that will simulate it: the one pairing that cannot be
a version apart on the day it is downloaded.

### Built on the host, like the web client

`scripts/stage-hassault-download.mjs` builds the client and writes it, with a
`manifest.json`, into `apps/native-fps/dist/`; the image copies that directory in.
It is not built in the image for the reason the browser game is not: a Linux
builder cannot produce a Windows executable without a cross toolchain, and the
machine that deploys already has the real one.

### Honest about what it knows

The manifest is written by the staging script and nothing here re-hashes 130 MB
per request, so the checksum is the script's claim about the file it staged. It is
only repeated while the file on disk is still the size the manifest recorded; a
file swapped underneath it is served (it is what is there) with no checksum,
rather than with one that describes something else. No manifest, or a deploy from
a machine that could not build for Windows, is not an error: `/download/info`
says `null` and the landing page shows no button.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

#: The name the file is served and saved under. A constant rather than whatever
#: the manifest says: it is in a URL people share, and it is the name of the thing
#: a player double-clicks.
FILENAME = "hAssault.exe"

router = APIRouter(prefix="/download", tags=["download"])


@dataclass(frozen=True)
class Build:
    """One staged client."""

    path: Path
    version: str
    commit: str
    size_bytes: int
    sha256: str
    built_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": FILENAME,
            "url": f"/download/{FILENAME}",
            "version": self.version,
            "commit": self.commit,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "builtAt": self.built_at,
        }


def download_dir() -> Path:
    """Where the staged client lives (`GAMES_DOWNLOAD_DIR`, else the checkout's
    `apps/native-fps/dist`). Read per call, so a test can point it somewhere."""
    default = Path(__file__).resolve().parents[2] / "apps" / "native-fps" / "dist"
    return Path(os.environ.get("GAMES_DOWNLOAD_DIR") or default)


def windows_build() -> Build | None:
    """The staged Windows client, or None when this deploy carries none."""
    root = download_dir()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = manifest.get("windows") if isinstance(manifest, dict) else None
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("file") or "")
    # A bare file name in the staging directory and nothing else. The manifest is
    # ours, but "ours" is one mistaken copy away from "a path somebody typed", and
    # this is the one route here that hands back a file from disk.
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    path = root / name
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if not path.is_file():
        return None
    recorded = entry.get("sizeBytes")
    return Build(
        path=path,
        version=str(entry.get("version") or ""),
        commit=str(entry.get("commit") or ""),
        size_bytes=size,
        sha256=str(entry.get("sha256") or "") if recorded == size else "",
        built_at=str(entry.get("builtAt") or ""),
    )


@router.get("/info")
def download_info() -> dict[str, Any]:
    """What can be downloaded from this server, per platform. `null` for a
    platform means there is no build of it here."""
    build = windows_build()
    return {"windows": build.to_dict() if build else None}


# HEAD as well as GET: a download manager asks for the size first, and FastAPI,
# unlike Starlette's own routes, does not answer HEAD for a GET route.
@router.api_route(f"/{FILENAME}", methods=["GET", "HEAD"])
def download_windows(request: Request) -> Response:
    build = windows_build()
    if build is None:
        raise HTTPException(
            status_code=404, detail="this server carries no Windows client"
        )
    headers = {
        # The name is stable and the bytes are not, so it must be revalidated:
        # a cached copy of last month's client is a client a deploy behind the
        # server it is about to join.
        "Cache-Control": "no-cache",
    }
    if build.sha256:
        headers["X-Checksum-Sha256"] = build.sha256
    try:
        stat = build.path.stat()
    except OSError as exc:  # gone between the manifest read and here
        raise HTTPException(status_code=404, detail="the client is gone") from exc
    # `stat_result` up front so the validators exist before the body is sent.
    # `FileResponse` answers `Range` and `If-Range` itself, which is what lets an
    # interrupted 130 MB download resume.
    response = FileResponse(
        build.path,
        media_type="application/vnd.microsoft.portable-executable",
        filename=FILENAME,
        headers=headers,
        stat_result=stat,
    )
    # It does **not** answer `If-None-Match` — only `StaticFiles` does — so a
    # revalidation would be the whole file again. Answered here, against the
    # ETag it just computed, so the two can never be different validators.
    etag = response.headers.get("etag", "")
    if etag and _matches(request.headers.get("if-none-match", ""), etag):
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"}
        )
    return response


def _matches(if_none_match: str, etag: str) -> bool:
    """Whether an `If-None-Match` header names this ETag (weak comparison: a
    proxy may have marked ours `W/`, and for "do you already have these bytes"
    the two are the same answer)."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return any(
        candidate.strip().removeprefix("W/") == bare
        for candidate in if_none_match.split(",")
    )
