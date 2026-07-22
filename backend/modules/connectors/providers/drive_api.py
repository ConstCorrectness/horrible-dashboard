"""Shared Google Drive access: authenticated calls, listing, and text extraction.

Used by both the agent tools (`google_tools`) and the library sync (`google_sync`), so
"how do we get text out of a Drive file" has exactly one answer. Errors come back as
`{"error": ...}` values rather than exceptions — the callers are an agent tool loop and
a background task, and neither should crash on a bad file.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.artifacts.pdftext import extract_pdf_text
from backend.modules.connectors.providers import google

__all__ = ["extract_pdf_text"]  # moved to artifacts.pdftext; re-exported for callers

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"

GOOGLE_DOC = "application/vnd.google-apps.document"
PDF = "application/pdf"
# Google-native types are exported rather than downloaded; everything else is fetched
# with alt=media. Sheets/Slides are deliberately out of scope for now — a spreadsheet
# flattened to plain text embeds badly and would pollute a library.
EXPORTABLE = {GOOGLE_DOC: "text/plain"}
PLAIN_TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "text/html",
}

# Anything we can turn into text — the sync's default filter.
READABLE_MIMES = {GOOGLE_DOC, PDF, *PLAIN_TEXT_MIMES}

NOT_CONNECTED = {
    "error": "Google isn't connected — connect it from the home page, then try again."
}

# Drive caps page_size at 1000; keep tool responses far smaller than that.
MAX_TOOL_RESULTS = 25
MAX_TEXT_CHARS = 100_000


async def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    raw: bool = False,
) -> Any:
    """One authenticated Drive call. `raw=True` returns bytes (for `alt=media`)."""
    import httpx

    token = await google.token()
    if not token:
        return NOT_CONNECTED
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.request(
                method,
                f"{API}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Google Drive: {exc}"}

    if res.status_code == 401:
        return {
            "error": "Google rejected the stored token — reconnect Google from the home page."
        }
    if res.status_code == 403:
        detail = _message(res)
        if "rate" in detail.lower() or "quota" in detail.lower():
            return {"error": "Google Drive rate limit hit — wait and try again."}
        return {"error": f"Google Drive refused the request: {detail}"}
    if res.status_code == 404:
        return {"error": "no such Drive file (or it isn't shared with this account)"}
    if res.status_code >= 400:
        return {"error": f"Google Drive returned {res.status_code}: {_message(res)}"}
    return res.content if raw else res.json()


def _message(res: Any) -> str:
    try:
        return str((res.json().get("error") or {}).get("message") or "")
    except (ValueError, AttributeError):
        return str(res.text)[:200]


FILE_FIELDS = "id, name, mimeType, modifiedTime, webViewLink, size, trashed"


async def list_files(
    *,
    query: str | None = None,
    page_token: str | None = None,
    page_size: int = 100,
    order_by: str | None = "modifiedTime desc",
) -> Any:
    """One page of `files.list`. Returns the raw response so callers can follow
    `nextPageToken` themselves."""
    params: dict[str, Any] = {
        "pageSize": page_size,
        "fields": f"nextPageToken, files({FILE_FIELDS})",
    }
    if query:
        params["q"] = query
    if page_token:
        params["pageToken"] = page_token
    if order_by:
        params["orderBy"] = order_by
    return await request("GET", "/files", params=params)


def readable_mime_query() -> str:
    """A `q` clause matching the file types we can extract text from."""
    types = " or ".join(f"mimeType='{m}'" for m in sorted(READABLE_MIMES))
    return f"({types}) and trashed = false"


async def extract_text(file_id: str, mime: str, name: str = "") -> str | dict[str, Any]:
    """The file's text, or an `{error}`.

    Google-native docs are *exported*; plain-text types are downloaded; PDFs are
    downloaded and parsed. An unsupported type is an error, not an empty string —
    silently ingesting nothing is how a sync looks healthy while doing nothing.
    """
    if mime in EXPORTABLE:
        data = await request(
            "GET",
            f"/files/{file_id}/export",
            params={"mimeType": EXPORTABLE[mime]},
            raw=True,
        )
        if isinstance(data, dict):
            return data
        return data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]

    if mime in PLAIN_TEXT_MIMES:
        data = await request(
            "GET", f"/files/{file_id}", params={"alt": "media"}, raw=True
        )
        if isinstance(data, dict):
            return data
        return data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]

    if mime == PDF:
        data = await request(
            "GET", f"/files/{file_id}", params={"alt": "media"}, raw=True
        )
        if isinstance(data, dict):
            return data
        return extract_pdf_text(data, name=name)

    return {"error": f"can't read {mime} as text"}
