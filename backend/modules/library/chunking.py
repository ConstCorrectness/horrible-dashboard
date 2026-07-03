"""Split extracted text into overlapping chunks for embedding.

Character-based and dependency-free: paragraphs are packed into ~`size`-char
windows (over-long paragraphs are hard-split), then a `overlap`-char tail of each
chunk is prepended to the next so a passage straddling a boundary is still
retrievable from both sides.
"""

from __future__ import annotations

import re

_PARA_SPLIT = re.compile(r"\n\s*\n")


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if overlap >= size:
        overlap = size // 4

    # 1) Units no larger than `size`: paragraphs, hard-split when a single one is
    #    over budget.
    units: list[str] = []
    for para in _PARA_SPLIT.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            units.append(para)
        else:
            units.extend(para[i : i + size] for i in range(0, len(para), size))

    # 2) Pack units into chunks up to `size`.
    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 2 + len(unit) <= size:
            current += "\n\n" + unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)

    # 3) Prepend an overlap tail from the previous chunk for retrieval context.
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            overlapped.append((prev[-overlap:] + "\n\n" + cur).strip())
        chunks = overlapped

    return chunks
