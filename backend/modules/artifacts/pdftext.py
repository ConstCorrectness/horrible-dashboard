"""Text extraction from PDF bytes — the store's side of the PDF pipeline.

Moved here from ``connectors/providers/drive_api.py`` so the library's ``pdf``
source ingestion and the Drive sync share one implementation. Uses ``pypdf``
(BSD) rather than PyMuPDF: PyMuPDF is AGPL-3.0, which is viral for
network-served software and would be a licensing decision, not a parsing one.
Imported lazily so nothing pays for it until a PDF actually arrives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 100_000


def extract_pdf_text(
    data: bytes, *, name: str = "", max_chars: int | None = None
) -> str | dict[str, Any]:
    """Text from PDF bytes, or ``{"error": ...}`` when there is none to give.

    ``max_chars`` defaults to the module's ``MAX_TEXT_CHARS`` at call time (tests
    monkeypatch the module global)."""
    import io

    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # An empty-password decrypt covers PDFs that are "protected" but readable.
            try:
                if not reader.decrypt(""):
                    return {"error": f"{name or 'PDF'} is password-protected"}
            except (PyPdfError, NotImplementedError):
                return {"error": f"{name or 'PDF'} is password-protected"}
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PyPdfError, ValueError, OSError) as exc:
        return {"error": f"couldn't parse {name or 'PDF'}: {exc}"}

    text = "\n\n".join(p.strip() for p in pages if p.strip()).strip()
    if not text:
        # A scanned PDF is images with no text layer. Say so — it isn't an empty doc,
        # and pretending otherwise files a blank source into the library.
        return {"error": f"{name or 'PDF'} has no extractable text (probably scanned)"}
    return text[: max_chars if max_chars is not None else MAX_TEXT_CHARS]


def extract_pdf_text_from_path(
    path: Path, *, name: str = "", max_chars: int | None = None
) -> str | dict[str, Any]:
    """`extract_pdf_text` over a stored blob."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"error": f"couldn't read {name or path.name}: {exc}"}
    return extract_pdf_text(data, name=name or path.name, max_chars=max_chars)
