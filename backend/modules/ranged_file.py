"""Serve a media file with HTTP Range support, so `<audio>`/`<video>` can seek.

Shared by karaoke and the music library. A media element issues Range requests for
every seek and, in Chromium, for the first load too; a server that ignores them
makes the scrubber snap back to zero.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

# Read this much per chunk when serving a byte range.
_STREAM_CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def ranged_file_response(
    path: Path, request: Request, *, default_mime: str = "application/octet-stream"
) -> Response:
    mime = mimetypes.guess_type(path.name)[0] or default_mime
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
