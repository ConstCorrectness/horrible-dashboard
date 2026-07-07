"""Pydantic models for the code-intelligence surface: symbol outlines, cross-repo
symbol hits, and the shared code locus. Lines/columns are **1-based** to match the
editor and diagnostics conventions. See docs/modules/code.mdx."""

from __future__ import annotations

from pydantic import BaseModel


class Position(BaseModel):
    line: int  # 1-based
    column: int  # 1-based


class Range(BaseModel):
    start: Position
    end: Position


class Symbol(BaseModel):
    """One definition found by the tree-sitter index."""

    name: str
    kind: str  # function | method | class | interface | type | enum
    range: Range
    container: str | None = None  # enclosing class/function name, if any


class DocumentSymbols(BaseModel):
    path: str
    language: str | None
    symbols: list[Symbol]


class SymbolHit(Symbol):
    """A `Symbol` plus the file it lives in — the shape cross-repo `find` returns."""

    path: str


class FindResult(BaseModel):
    query: str
    hits: list[SymbolHit]


class SemanticHit(BaseModel):
    """A semantic-search result: a definition plus its cosine score. Fields are
    optional because they're read back from stored metadata."""

    name: str | None = None
    kind: str | None = None
    container: str | None = None
    path: str | None = None
    range: Range | None = None
    score: float


class SemanticSearchResult(BaseModel):
    query: str
    building: bool  # a reindex is in flight (results may be empty/partial)
    results: list[SemanticHit]


class ReindexResult(BaseModel):
    started: bool
    indexed: int | None = None


class Locus(BaseModel):
    """The shared 'what code am I looking at' cursor. All fields optional so a bare
    cursor (path + line) and a full symbol selection use the same shape."""

    path: str | None = None
    root: str | None = None
    range: Range | None = None
    symbol: str | None = None
    source: str | None = None  # which pane/agent drove this update
    origin: str | None = None  # client instance id, for self-echo suppression
