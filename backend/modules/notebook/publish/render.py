"""A notebook as one standalone HTML page, via nbconvert (BSD-3).

nbconvert's `lab` template with images embedded, so the page is a single file that
GitHub Pages or any static host can serve. Two things worth knowing:

- **Math loads MathJax from its CDN.** Fine on a published page; a copy opened offline
  shows raw TeX.
- **HTML outputs are passed through, not sanitized.** This is the author's own notebook
  on the author's own site, the same trusted-local posture the notebook pane takes when
  it renders them.

CPU-bound and synchronous: call it through `asyncio.to_thread`.
"""

from __future__ import annotations

from typing import Any


class RenderError(RuntimeError):
    """Rendering failed in a way worth showing the person verbatim."""


def to_html(nb: Any, *, title: str) -> str:
    try:
        from nbconvert import HTMLExporter
    except ImportError as exc:  # pragma: no cover — core dependency
        raise RenderError(f"nbconvert is not installed: {exc}") from exc
    exporter = HTMLExporter(template_name="lab")
    exporter.embed_images = True
    try:
        body, _resources = exporter.from_notebook_node(
            nb, resources={"metadata": {"name": title}}
        )
    except Exception as exc:
        raise RenderError(f"Could not render the notebook to HTML: {exc}") from exc
    return body
