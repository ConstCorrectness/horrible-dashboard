"""PDF text extraction for the Drive sync.

The file this replaced skipped PDFs outright ("requires PyMuPDF byte parsing"), so
these cover the parser for real — against actual PDF bytes, not a stub. The PDFs are
hand-built rather than pulled from a fixture or a new dependency: a minimal one-page
PDF is a few hundred bytes and keeps the test honest about what pypdf receives.
"""

from __future__ import annotations

from backend.modules.connectors.providers.drive_api import extract_pdf_text


def make_pdf(text: str | None) -> bytes:
    """A minimal one-page PDF. `text=None` produces a page with no text layer — what a
    scanned document looks like to a parser."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
        b"/MediaBox[0 0 612 792]/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    content = b"" if text is None else f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objs.append(b"<</Length %d>>stream\n%s\nendstream" % (len(content), content))

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref,
    )
    return bytes(out)


def test_extracts_text_from_a_real_pdf():
    result = extract_pdf_text(make_pdf("Quarterly revenue was up"), name="report.pdf")
    assert isinstance(result, str)
    assert "Quarterly revenue was up" in result


def test_a_scanned_pdf_reports_rather_than_returning_empty():
    """A PDF with no text layer is images. Returning "" would file a blank source into
    the library and look like a successful sync."""
    result = extract_pdf_text(make_pdf(None), name="scan.pdf")
    assert isinstance(result, dict)
    assert "no extractable text" in result["error"]
    assert "scan.pdf" in result["error"]


def test_garbage_bytes_are_an_error_not_a_crash():
    # One bad file must not take down a whole sync run.
    result = extract_pdf_text(b"this is not a pdf at all", name="junk.pdf")
    assert isinstance(result, dict)
    assert "junk.pdf" in result["error"]


def test_empty_bytes_are_an_error():
    assert isinstance(extract_pdf_text(b""), dict)


def test_long_pdf_text_is_truncated(monkeypatch):
    from backend.modules.connectors.providers import drive_api

    monkeypatch.setattr(drive_api, "MAX_TEXT_CHARS", 10)
    result = drive_api.extract_pdf_text(make_pdf("abcdefghijklmnopqrstuvwxyz"))
    assert isinstance(result, str)
    assert len(result) == 10
