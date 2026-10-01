"""The adapter contract, and the small helpers every adapter needs.

An adapter **delegates to the client that already owns its upstream** (the HF
connector, `arxiv.client`, the Kaggle provider, `mcp.catalog`, …) rather than
opening a second one: token handling, throttling and error wording then live in one
place, and the agent's view of a source and the pane's cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.modules.discover.models import (
    DiscoverDetail,
    DiscoverItem,
    SourceSpec,
    Status,
)

#: Rows per page. Enough to scan, small enough that a page is one cheap call.
PAGE_SIZE = 30


@dataclass
class Query:
    kind: str
    q: str = ""
    sort: str = ""
    filters: dict[str, str] = field(default_factory=dict)
    cursor: str | None = None

    def filter(self, key: str, default: str = "") -> str:
        """The filter's value; `default` only when the caller didn't send it at all.

        An explicit empty string means "any" and must not snap back to the default,
        or a user could never clear a default topic.
        """
        value = self.filters.get(key)
        return (default if value is None else value).strip()


def header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup over a plain dict."""
    name = name.lower()
    return next((v for k, v in headers.items() if k.lower() == name), None)


@dataclass
class SourceResult:
    items: list[DiscoverItem]
    feed_label: str = ""
    cursor_next: str | None = None
    total: int | None = None
    #: A partial answer (a curated overlay without the live registry, say) is
    #: still a result — but the pane has to say it is partial.
    status: Status = "ok"
    message: str | None = None


class SourceUnavailable(RuntimeError):
    """Upstream couldn't answer. `status` says whose problem it is."""

    def __init__(
        self,
        message: str,
        *,
        status: Status = "error",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status: Status = status
        self.retry_after = retry_after


class DiscoverSource(Protocol):
    id: str

    async def spec(self) -> SourceSpec: ...

    async def list(self, query: Query) -> SourceResult: ...

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        """`known` is the row from a recent list call, when there is one — a source
        with no cheap detail endpoint renders from it instead of refetching."""
        ...


def num(value: Any) -> float | None:
    """A number from upstream, or None. `bool` is not a number here."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def iso(value: Any) -> str | None:
    """An ISO-8601 string from a str or datetime; None for anything else."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def strip_front_matter(markdown: str) -> str:
    """Drop a leading `---` YAML block — card metadata, not prose to render."""
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end != -1:
            return markdown[end + 4 :].lstrip("\n")
    return markdown


def front_matter(markdown: str) -> dict[str, str]:
    """Top-level `key: value` pairs from a leading YAML block.

    Enough for SKILL.md's `name`/`description`, including the block scalars authors
    use for long descriptions (`description: >` folded, `|` literal), whose text is on
    the indented lines that follow. Nested mappings are not needed and not parsed.
    """
    if not markdown.startswith("---"):
        return {}
    end = markdown.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    lines = markdown[3:end].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if ":" not in line or line.startswith((" ", "\t", "-", "#")):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value[:1] in (">", "|"):
            literal = value[0] == "|"
            block: list[str] = []
            while i < len(lines) and (
                not lines[i].strip() or lines[i][:1] in (" ", "\t")
            ):
                block.append(lines[i].strip())
                i += 1
            joiner = "\n" if literal else " "
            value = joiner.join(b for b in block if b or literal).strip()
        out[key.strip()] = value.strip("'\"")
    return out
