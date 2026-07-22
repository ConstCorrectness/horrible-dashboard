"""Research capture services: save a URL as a self-contained page, or fetch a PDF.

This is the **no-engine** capture path — everything fetched here goes through the
browser module's SSRF guard (`safe_fetch_html` / `safe_fetch_bytes`), redirects
re-validated per hop, sizes capped in transit. The engine path (live Chromium
session, cookies included) lives in `browser/session.py::_capture_page`; both
produce the same thing: a `page` artifact + a library source.

An external `single-file-cli` (AGPL — user-installed, never bundled; the process
boundary is the point) can replace the native inliner when the
`research.singleFileCli` setting names its executable. Its own fetching is not
hop-guarded — we validate the initial URL and document the rest as the user's
explicit opt-in.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from backend.modules.artifacts.store import store_bytes
from backend.modules.browser.fetch import (
    UnsafeUrlError,
    _validate,
    safe_fetch_bytes,
    safe_fetch_html,
)
from backend.modules.library.extract import extract_article
from backend.modules.library.models import IngestRequest
from backend.modules.library.routes import add_source
from backend.modules.research.capture import (
    IMAGE_KIND,
    STYLESHEET_KIND,
    build_page,
    filename_for_title,
    list_css_urls,
    list_resources,
)
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

_FETCH_PARALLELISM = 6
_ACCEPT_BY_KIND = {
    IMAGE_KIND: ("image/",),
    STYLESHEET_KIND: ("text/css", "text/plain"),
    # Assets referenced from within CSS: fonts and images.
    "css-asset": ("image/", "font/", "application/font", "application/x-font"),
}
_PDF_MAX_BYTES = 50_000_000
_SINGLE_FILE_TIMEOUT_S = 60


async def _fetch_typed(url: str, kind: str) -> tuple[str, tuple[bytes, str] | None]:
    """Like `_fetch_resource` but keeps the content-type for the data: URI."""
    try:
        from backend.modules.browser.fetch import _fetch_guarded

        final_url, resp = await _fetch_guarded(
            url,
            accept=_ACCEPT_BY_KIND.get(kind, ("image/",)),
            max_bytes=5_000_000,
        )
        del final_url
        mime = (resp.headers.get("content-type") or "").split(";")[0].strip()
        return url, (resp.content, mime)
    except Exception:  # noqa: BLE001 — per-resource failure is non-fatal by design
        return url, None


async def capture_url(
    url: str,
    *,
    library: str = "default",
    title: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch `url`, inline its resources, store the page artifact, and file it
    into the library. Returns `{"artifact": ..., "source": ...}` dicts."""
    cli = str(get_value("research.singleFileCli", "") or "").strip()
    if cli:
        page_html, final_url = await _capture_via_single_file(cli, url)
    else:
        final_url, html = await safe_fetch_html(url)
        page_html = await _inline_all(html, final_url)

    article = extract_article(page_html, final_url)
    resolved_title = title or article.title or final_url
    artifact = store_bytes(
        page_html.encode("utf-8"),
        kind="page",
        mime="text/html",
        filename=filename_for_title(resolved_title),
        origin_url=final_url,
        meta={"title": resolved_title, "engine": "single-file-cli" if cli else "fetch"},
    )
    source = await add_source(
        IngestRequest(
            type="page",
            library=library,
            url=final_url,
            title=resolved_title,
            tags=tags or [],
            artifact_id=artifact["id"],
        )
    )
    return {"artifact": artifact, "source": source.model_dump()}


async def _inline_all(html: str, base_url: str) -> str:
    """The two-pass inline protocol over the guarded async fetcher."""
    plan = list_resources(html, base_url)
    semaphore = asyncio.Semaphore(_FETCH_PARALLELISM)

    async def bounded(u: str, kind: str) -> tuple[str, tuple[bytes, str] | None]:
        async with semaphore:
            return await _fetch_typed(u, kind)

    fetched = await asyncio.gather(*(bounded(u, k) for u, k in plan.items()))
    resources = {u: r for u, r in fetched if r is not None}

    nested_plan: dict[str, str] = {}
    for res_url, kind in plan.items():
        if kind != STYLESHEET_KIND or res_url not in resources:
            continue
        css_text = resources[res_url][0].decode("utf-8", errors="replace")
        for nested in list_css_urls(css_text, res_url):
            if nested not in resources:
                nested_plan.setdefault(nested, "css-asset")
    if nested_plan:
        fetched = await asyncio.gather(*(bounded(u, k) for u, k in nested_plan.items()))
        resources.update({u: r for u, r in fetched if r is not None})

    return build_page(html, base_url, resources)


async def _capture_via_single_file(cli: str, url: str) -> tuple[str, str]:
    """Run the user-installed single-file-cli and return `(html, url)`.

    The CLI drives its own Chromium and fetches for itself, so only the initial
    URL is validated here — enabling the CLI is an explicit opt-in
    (`research.singleFileCli`) documented with exactly this caveat.
    """
    exe = shutil.which(cli)
    if exe is None:
        raise RuntimeError(
            f"research.singleFileCli is set to {cli!r} but no such executable is on PATH"
        )
    await asyncio.to_thread(_validate, url)
    proc = await asyncio.create_subprocess_exec(
        exe,
        url,
        "--dump-content",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_SINGLE_FILE_TIMEOUT_S
        )
    except TimeoutError:
        proc.kill()
        raise RuntimeError("single-file-cli timed out") from None
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"single-file-cli failed ({proc.returncode}): {detail}")
    html = stdout.decode("utf-8", errors="replace")
    if not html.strip():
        raise RuntimeError("single-file-cli produced no output")
    return html, url


async def save_pdf_url(
    url: str,
    *,
    library: str = "default",
    title: str | None = None,
    tags: list[str] | None = None,
    source_url: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """Fetch a PDF by URL (guarded), store it, and file it into the library.

    `source_url` overrides the catalog row's URL when the *citable* page differs
    from the byte URL (an arXiv abstract page vs. its /pdf endpoint)."""
    final_url, data = await safe_fetch_bytes(
        url,
        accept=("application/pdf", "application/octet-stream"),
        max_bytes=_PDF_MAX_BYTES,
    )
    if not data.startswith(b"%PDF-"):
        # octet-stream is in the allowlist because so many servers mislabel PDFs;
        # the magic check is what actually decides.
        raise UnsafeUrlError("response is not a PDF")
    name = title or final_url.rsplit("/", 1)[-1] or "document"
    filename = (
        name if name.lower().endswith(".pdf") else filename_for_title(name, "pdf")
    )
    artifact = store_bytes(
        data,
        kind="pdf",
        mime="application/pdf",
        filename=filename,
        origin_url=final_url,
        meta={"title": title or name},
    )
    source = await add_source(
        IngestRequest(
            type="pdf",
            library=library,
            url=source_url or final_url,
            title=title,
            author=author,
            tags=tags or [],
            artifact_id=artifact["id"],
        )
    )
    return {"artifact": artifact, "source": source.model_dump()}
