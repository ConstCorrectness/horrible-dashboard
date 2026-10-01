"""Documentation: find a doc set, then search inside it.

Two kinds:

- **`set`** — doc sets you can open. The pinned list is resolved against DevDocs'
  catalog (`devdocs.io/docs.json`, ~800 sets with exact upstream versions), so
  "Python" means the newest Python DevDocs has, with its version shown. Hugging Face's
  library docs aren't on DevDocs and have no public search API; they're listed as
  sets you can **capture** with the docviewer module, after which they're searchable
  locally. Sets already captured are listed too.
- **`entry`** — a search *inside* one DevDocs set, over its entry index
  (`documents.devdocs.io/{slug}/index.json`: every class, function and guide page,
  by name and type). These files are versioned and static, so each is fetched once
  and cached for a day.

DevDocs rejects requests without a User-Agent, so one is always sent.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from backend.modules.discover.models import (
    Badge,
    DiscoverDetail,
    DiscoverItem,
    Fact,
    FilterSpec,
    KindSpec,
    Link,
    Metric,
    Option,
    SourceSpec,
)
from backend.modules.discover.sources.base import Query, SourceResult, SourceUnavailable

CATALOG_URL = "https://devdocs.io/docs.json"
INDEX_URL = "https://documents.devdocs.io/{slug}/index.json"
_UA = {"User-Agent": "horrible-dashboard/0.1 (discover; docs browser)"}
CATALOG_TTL_S = 24 * 3600.0
INDEX_TTL_S = 24 * 3600.0
MAX_ENTRY_HITS = 60

#: Families pinned at the top, in this order. Each resolves to its newest DevDocs set.
PINNED = [
    "python",
    "pytorch",
    "numpy",
    "pandas",
    "scikit_learn",
    "tensorflow",
    "matplotlib",
    "fastapi",
    "typescript",
    "javascript",
    "react",
    "node",
    "rust",
]

#: Hugging Face library docs. Real URLs; searchable only after a docviewer capture.
HF_DOCS = [
    ("transformers", "Transformers"),
    ("datasets", "Datasets"),
    ("huggingface_hub", "Hub client library"),
    ("diffusers", "Diffusers"),
    ("peft", "PEFT"),
    ("trl", "TRL"),
    ("accelerate", "Accelerate"),
    ("tokenizers", "Tokenizers"),
    ("hub", "Hub documentation"),
]

_catalog: tuple[float, list[dict[str, Any]]] | None = None
_indexes: dict[str, tuple[float, list[dict[str, str]]]] = {}
_lock = asyncio.Lock()


async def _get_json(url: str) -> Any:
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=_UA
        ) as client:
            res = await client.get(url)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"couldn't reach DevDocs: {exc}") from exc
    if res.status_code != 200:
        raise SourceUnavailable(f"DevDocs returned {res.status_code} for {url}")
    try:
        return res.json()
    except ValueError as exc:
        raise SourceUnavailable("DevDocs returned unreadable JSON") from exc


async def catalog() -> list[dict[str, Any]]:
    global _catalog
    if _catalog and time.time() - _catalog[0] < CATALOG_TTL_S:
        return _catalog[1]
    async with _lock:
        if _catalog and time.time() - _catalog[0] < CATALOG_TTL_S:
            return _catalog[1]
        data = await _get_json(CATALOG_URL)
        rows = (
            [r for r in data if isinstance(r, dict) and r.get("slug")]
            if isinstance(data, list)
            else []
        )
        _catalog = (time.time(), rows)
        return rows


def _version_key(row: dict[str, Any]) -> tuple[int, ...]:
    release = str(row.get("release") or row.get("version") or "")
    nums = tuple(int(n) for n in re.findall(r"\d+", release)[:4])
    # Unversioned slugs (`react`) are DevDocs' "current"; versioned siblings like
    # `react~18` are older — prefer the bare one on ties by mtime.
    return (
        (*nums, int(row.get("mtime") or 0)) if nums else (0, int(row.get("mtime") or 0))
    )


def newest(rows: list[dict[str, Any]], family: str) -> dict[str, Any] | None:
    matches = [r for r in rows if str(r["slug"]).split("~", 1)[0] == family]
    if not matches:
        return None
    bare = [r for r in matches if r["slug"] == family]
    return bare[0] if bare else max(matches, key=_version_key)


def set_item(row: dict[str, Any], *, pinned: bool = False) -> DiscoverItem:
    slug = str(row["slug"])
    links = row.get("links") or {}
    release = row.get("release") or row.get("version")
    badges = [Badge(label="DevDocs")]
    if pinned:
        badges.insert(0, Badge(label="pinned", tone="info"))
    return DiscoverItem(
        source="docs",
        kind="set",
        id=slug,
        title=f"{row.get('name') or slug}{f' {release}' if release else ''}",
        subtitle=slug,
        # `attribution` is HTML; the name and version say what matters.
        description=f"The {row.get('name') or slug} reference, indexed by DevDocs.",
        url=f"https://devdocs.io/{slug}/",
        badges=badges,
        metrics=[
            Metric(
                key="size", label="index size", value=row.get("db_size"), unit="bytes"
            )
        ],
        facts=[
            Fact(label=label, value=str(value))
            for label, value in (
                ("DevDocs slug", slug),
                ("upstream version", release),
                ("homepage", links.get("home")),
                ("source", links.get("code")),
            )
            if value
        ],
    )


def hf_item(lib: str, title: str, captured: dict[str, dict[str, Any]]) -> DiscoverItem:
    url = f"https://huggingface.co/docs/{lib}"
    have = next(
        (s for s in captured.values() if str(s.get("seed_url", "")).startswith(url)),
        None,
    )
    badges = [Badge(label="Hugging Face")]
    badges.insert(
        0,
        Badge(label=f"captured · {have.get('page_count', 0)} pages", tone="ok")
        if have
        else Badge(
            label="capture to search",
            title="No public search API; capture it with the doc viewer",
        ),
    )
    return DiscoverItem(
        source="docs",
        kind="set",
        id=f"hf:{lib}",
        title=f"HF {title}",
        subtitle=url,
        description=f"Hugging Face {title} documentation.",
        url=url,
        badges=badges,
    )


def _captured() -> dict[str, dict[str, Any]]:
    try:
        from backend.modules.docviewer import store  # noqa: PLC0415

        return {str(s["id"]): s for s in store.list_sets()}
    except Exception:  # noqa: BLE001 — docviewer's DB missing must not break browsing
        return {}


def captured_item(row: dict[str, Any]) -> DiscoverItem:
    return DiscoverItem(
        source="docs",
        kind="set",
        id=f"captured:{row['id']}",
        title=str(row.get("title") or row.get("seed_url")),
        subtitle=str(row.get("seed_url") or ""),
        description="Captured with the doc viewer — searchable in the library.",
        url=str(row.get("seed_url") or "") or None,
        badges=[
            Badge(
                label=str(row.get("status") or "captured"),
                tone="ok" if row.get("status") == "ready" else "idle",
            )
        ],
        metrics=[Metric(key="pages", label="pages", value=row.get("page_count"))],
        updated_at=row.get("last_crawled_at"),
    )


async def index(slug: str) -> list[dict[str, str]]:
    hit = _indexes.get(slug)
    if hit and time.time() - hit[0] < INDEX_TTL_S:
        return hit[1]
    data = await _get_json(INDEX_URL.format(slug=slug))
    entries = [
        {
            "name": str(e.get("name") or ""),
            "path": str(e.get("path") or ""),
            "type": str(e.get("type") or ""),
        }
        for e in (data or {}).get("entries") or []
        if isinstance(e, dict) and e.get("name")
    ]
    _indexes[slug] = (time.time(), entries)
    return entries


def rank(entries: list[dict[str, str]], q: str) -> list[dict[str, str]]:
    """Name prefix beats word-start beats substring; shorter names first within a tier.

    `q` matches case-insensitively against the entry name, and also against its
    type (`torch.nn` finds the `torch.nn` section's members).
    """
    needle = q.strip().lower()
    if not needle:
        return entries[:MAX_ENTRY_HITS]
    scored: list[tuple[int, int, dict[str, str]]] = []
    for entry in entries:
        name = entry["name"].lower()
        if name == needle:
            tier = 0
        elif name.startswith(needle):
            tier = 1
        elif re.search(rf"(^|[\s._(:]){re.escape(needle)}", name):
            tier = 2
        elif needle in name:
            tier = 3
        elif needle in entry["type"].lower():
            tier = 4
        else:
            continue
        scored.append((tier, len(name), entry))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [e for _, _, e in scored[:MAX_ENTRY_HITS]]


def entry_item(slug: str, set_name: str, entry: dict[str, str]) -> DiscoverItem:
    return DiscoverItem(
        source="docs",
        kind="entry",
        id=f"{slug}/{entry['path']}",
        title=entry["name"],
        subtitle=f"{set_name} · {entry['type']}" if entry["type"] else set_name,
        url=f"https://devdocs.io/{slug}/{entry['path']}",
        facts=[
            Fact(label="doc set", value=set_name),
            Fact(label="section", value=entry["type"] or "—"),
            Fact(label="path", value=entry["path"]),
        ],
    )


class DocsSource:
    id = "docs"

    async def spec(self) -> SourceSpec:
        try:
            rows = await catalog()
        except SourceUnavailable:
            rows = []
        sets = [r for r in (newest(rows, f) for f in PINNED) if r]
        options = [
            Option(
                value=str(r["slug"]),
                label=f"{r.get('name')} {r.get('release') or ''}".strip(),
            )
            for r in sets
        ] or [Option(value="python~3.14", label="Python")]
        return SourceSpec(
            id=self.id,
            label="Docs",
            kinds=[
                KindSpec(
                    id="entry",
                    label="Search docs",
                    filters=[
                        FilterSpec(
                            id="set",
                            label="Doc set",
                            options=options,
                            default=options[0].value,
                        )
                    ],
                    search_placeholder="Search a doc set — class, function, guide…",
                ),
                KindSpec(
                    id="set",
                    label="Doc sets",
                    search_placeholder="Find a doc set — 800+ on DevDocs…",
                ),
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        rows = await catalog()
        if query.kind == "set":
            captured = _captured()
            if query.q:
                needle = query.q.lower()
                hits = [
                    r
                    for r in rows
                    if needle in str(r.get("name", "")).lower()
                    or needle in str(r["slug"]).lower()
                ]
                # One row per family (newest), unless the user typed a version.
                if "~" not in needle and not re.search(r"\d", needle):
                    families = dict.fromkeys(
                        str(r["slug"]).split("~", 1)[0] for r in hits
                    )
                    hits = [r for r in (newest(rows, f) for f in families) if r]
                items = [set_item(r) for r in hits[:60]]
                items += [
                    hf_item(lib, t, captured)
                    for lib, t in HF_DOCS
                    if needle in f"hf {lib} {t}".lower()
                ]
                return SourceResult(
                    items=items,
                    feed_label=f"Doc sets matching “{query.q}”",
                    total=len(items),
                )
            items = [
                set_item(r, pinned=True) for r in (newest(rows, f) for f in PINNED) if r
            ]
            items += [hf_item(lib, t, captured) for lib, t in HF_DOCS]
            items += [captured_item(s) for s in captured.values()]
            return SourceResult(
                items=items,
                feed_label="Pinned doc sets (newest version on DevDocs), Hugging Face docs, and sets you've captured",
                total=len(items),
            )

        slug = query.filter("set") or next(
            (str(r["slug"]) for r in (newest(rows, "python"),) if r), "python~3.14"
        )
        meta = next((r for r in rows if r["slug"] == slug), None)
        if meta is None:
            raise SourceUnavailable(f"DevDocs has no doc set {slug!r}")
        set_name = f"{meta.get('name')} {meta.get('release') or ''}".strip()
        entries = await index(slug)
        hits = rank(entries, query.q)
        return SourceResult(
            items=[entry_item(slug, set_name, e) for e in hits],
            feed_label=(
                f"{set_name} entries matching “{query.q}”"
                if query.q
                else f"{set_name} — first entries of {len(entries):,}; type to search them"
            ),
            total=len(entries) if not query.q else len(hits),
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        if (
            known is None
            and kind == "set"
            and not item_id.startswith(("hf:", "captured:"))
        ):
            row = next((r for r in await catalog() if r["slug"] == item_id), None)
            known = set_item(row) if row else None
        if known is None:
            raise SourceUnavailable(
                "open this item from a list first", status="degraded"
            )
        links = [Link(label="Open", url=known.url)] if known.url else []
        body = known.description
        if kind == "set" and item_id.startswith("hf:"):
            body = (
                "Hugging Face doesn't offer a public search API for its docs. Capture this "
                "set with the doc viewer to crawl it into the library; it becomes "
                "searchable locally, and readable offline."
            )
        return DiscoverDetail(
            item=known, body=body, body_format="text", facts=known.facts, links=links
        )
