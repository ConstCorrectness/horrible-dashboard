"""App-owned code symbol index (tree-sitter). A process-global service like the LSP
spine and telemetry recorder: per-file outlines (mtime-cached) and cross-root fuzzy
symbol search. Defs only. Feeds the outline pane, the `symbols.*` agent tools, and —
later — semantic search and provenance, which reuse this same substrate rather than
standing up their own. See docs/modules/code.mdx."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from backend.modules.code.models import Symbol, SymbolHit
from backend.modules.code.ts import extract_symbols, lang_for_path

_SKIP_DIRS = {
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".next",
    ".git",
    ".venv",
    "venv",
}
_MAX_FILE_BYTES = 1_500_000


class CodeIndex:
    def __init__(self) -> None:
        # path -> (mtime, symbols). One outline per file, reparsed only when the file's
        # mtime changes — so the outline is always fresh with no explicit save hook.
        self._doc: dict[str, tuple[float, list[Symbol]]] = {}

    def document_symbols(self, path: Path) -> list[Symbol]:
        """Definitions in one file, cached by mtime. Empty for unindexed languages
        or unreadable files (never raises — a bad file just has no outline)."""
        lang = lang_for_path(path)
        if lang is None:
            return []
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        cached = self._doc.get(str(path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        syms = extract_symbols(lang, text)
        self._doc[str(path)] = (mtime, syms)
        return syms

    def find_symbols(
        self, query: str, roots: list[Path], limit: int = 50
    ) -> list[SymbolHit]:
        # Rebuild from the per-file mtime cache each call: unchanged files don't
        # reparse, so results are always fresh without a save-invalidation hook.
        allsyms = self._build(roots)
        q = query.strip().lower()
        if not q:
            return allsyms[:limit]
        scored = [(s, h) for h in allsyms if (s := _fuzzy(q, h.name.lower())) > 0]
        scored.sort(key=lambda sh: (-sh[0], len(sh[1].name), sh[1].name.lower()))
        return [h for _, h in scored[:limit]]

    def _build(self, roots: list[Path]) -> list[SymbolHit]:
        hits: list[SymbolHit] = []
        for root in roots:
            for path in _walk_source_files(root):
                for sym in self.document_symbols(path):
                    hits.append(SymbolHit(**sym.model_dump(), path=str(path)))
        return hits


def _walk_source_files(root: Path) -> Iterator[Path]:
    """Indexable source files under `root`, skipping vendored/build/hidden dirs."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
            elif lang_for_path(entry) is not None:
                try:
                    if entry.stat().st_size <= _MAX_FILE_BYTES:
                        yield entry
                except OSError:
                    continue


def _fuzzy(q: str, name: str) -> float:
    """Match score in (0, 1]; 0 = no match. Exact > prefix > substring > subsequence,
    each normalized by name length so tighter matches on shorter names rank first."""
    if q == name:
        return 1.0
    if name.startswith(q):
        return 0.9
    if q in name:
        return 0.7 * len(q) / len(name)
    i = 0
    for ch in name:
        if i < len(q) and ch == q[i]:
            i += 1
    return 0.4 * len(q) / len(name) if i == len(q) else 0.0


# Process-global singleton (import and use directly, like `recorder`).
code_index = CodeIndex()
