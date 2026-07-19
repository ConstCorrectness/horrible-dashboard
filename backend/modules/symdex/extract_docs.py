"""Project-docs extraction for the symdex index.

Chunks every `docs/**/*.mdx` page (the same tree the Docusaurus site publishes)
with the library's `chunk_text`, one document per chunk — so the agents can
answer "how does X work in this app" from the authoritative docs. Sync and
file-bound — call it on a thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.modules.library.chunking import chunk_text

# backend/modules/symdex/extract_docs.py → repo root is three levels up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 150

_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass
class DocChunk:
    id: str
    text: str
    metadata: dict[str, Any]


def extract_docs(docs_dir: Path | None = None) -> list[DocChunk]:
    root = docs_dir if docs_dir is not None else _REPO_ROOT / "docs"
    out: list[DocChunk] = []
    if not root.is_dir():
        return out
    for page in sorted(root.rglob("*.mdx")):
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = page.relative_to(root).as_posix()
        heading = _HEADING.search(text)
        title = heading.group(1).strip() if heading else page.stem
        for i, chunk in enumerate(chunk_text(text, _CHUNK_SIZE, _CHUNK_OVERLAP)):
            out.append(
                DocChunk(
                    id=f"doc:{rel}#{i}",
                    text=f"{title} ({rel})\n{chunk}",
                    metadata={"path": rel, "title": title, "chunk_index": i},
                )
            )
    return out
