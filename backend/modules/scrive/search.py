"""`scrive.searchSite`: find what a site already has — past posts, scenes, templates —
so the agent links to and reuses it instead of writing it again (Kombai's context
index).

Two rankers, fused by rank (RRF, as the library does — their scores share no
scale):

- **keywords**, always: titles, headings and text of every page, scene and site
  template, scored by where the query's words occur. Instant, needs nothing.
- **meaning**, when the site has a semantic index (`index.py`) and an embedding model
  answers: finds "the post about priors" from a query that never says "prior".
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from pydantic import BaseModel, Field

from backend.modules.scrive import index, sections, store, templates
from backend.modules.search.fusion import fuse

_WORD = re.compile(r"[a-z0-9][a-z0-9+#.-]*", re.I)
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "what",
    "when", "where", "which", "with", "about", "my", "post", "page",
}  # fmt: skip


class Hit(BaseModel):
    path: str
    kind: str  # post | page | scene | template
    title: str
    snippets: list[str] = Field(default_factory=list)
    matched_by: list[str] = Field(default_factory=list)


def terms_of(query: str) -> list[str]:
    words = [w.lower().strip(".-") for w in _WORD.findall(query)]
    return [w for w in words if len(w) > 1 and w not in _STOP] or [
        w for w in words if w
    ]


def _documents(site_id: str) -> list[tuple[str, str, str, str]]:
    """`(path, kind, title, text)` for every searchable file of a site."""
    base = store.site_dir(site_id)
    docs: list[tuple[str, str, str, str]] = []
    for meta in store.list_pages(site_id):
        if not meta.path.endswith(".md"):
            continue
        text = (base / meta.path).read_bytes().decode("utf-8", "replace")
        docs.append((meta.path, meta.kind, meta.title, text))
    scenes = base / "scenes"
    if scenes.is_dir():
        for path in sorted(scenes.rglob("*.tsx")):
            rel = path.relative_to(base).as_posix()
            docs.append(
                (rel, "scene", Path(rel).stem, path.read_text("utf-8", "replace"))
            )
    for info in templates.list_templates(site_id):
        if info.source == "site":
            text = (base / templates.SITE_DIR / f"{info.id}.md").read_text(
                "utf-8", "replace"
            )
            docs.append(
                (f"{templates.SITE_DIR}/{info.id}.md", "template", info.name, text)
            )
    return docs


def _snippets(text: str, terms: list[str], limit: int = 2) -> list[str]:
    lines = [line.rstrip("\r") for line in text.split("\n")]
    heading = ""
    scored: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = sections.HEADING_RE.match(line)
        if m:
            heading = (m.group(2) or "").strip()
            continue
        low = line.lower()
        hits = sum(1 for t in terms if t in low)
        if hits and line.strip():
            where = f"[{heading}] " if heading else ""
            scored.append((hits, -i, where + line.strip()[:200]))
    scored.sort(reverse=True)
    return [s for _h, _i, s in scored[:limit]]


def keyword_hits(site_id: str, query: str) -> list[tuple[float, Hit]]:
    terms = terms_of(query)
    if not terms:
        return []
    phrase = query.strip().lower()
    out: list[tuple[float, Hit]] = []
    for path, kind, title, text in _documents(site_id):
        low_title = title.lower()
        low = text.lower()
        heads = " ".join(h.text.lower() for h in sections.headings(text))
        matched = 0
        score = 0.0
        for t in terms:
            body = low.count(t)
            if not body and t not in low_title and t not in path.lower():
                continue
            matched += 1
            score += 5 * (t in low_title) + 3 * (t in heads) + 2 * (t in path.lower())
            score += math.log1p(body)
        if matched * 2 < len(terms):
            continue
        if len(phrase) > 3 and phrase in low:
            score += 4
        score *= matched / len(terms)
        out.append(
            (
                score,
                Hit(
                    path=path,
                    kind=kind,
                    title=title,
                    snippets=_snippets(text, terms),
                    matched_by=["keywords"],
                ),
            )
        )
    out.sort(key=lambda pair: pair[0], reverse=True)
    return out


async def search_site(site_id: str, query: str, limit: int = 8) -> dict:
    keyword = keyword_hits(site_id, query)
    by_path = {hit.path: hit for _s, hit in keyword}
    semantic = await index.search(site_id, query, limit)
    for row in semantic:
        hit = by_path.get(row["path"])
        if hit is None:
            kind = "post" if row["path"].startswith("posts/") else "page"
            hit = by_path[row["path"]] = Hit(
                path=row["path"], kind=kind, title=row["title"]
            )
        if "meaning" not in hit.matched_by:
            hit.matched_by.append("meaning")
            excerpt = row["text"].split("\n", 1)[-1].strip().replace("\n", " ")[:200]
            if excerpt and len(hit.snippets) < 3:
                hit.snippets.append(excerpt)
    ranked = fuse([[h.path for _s, h in keyword], [r["path"] for r in semantic]])
    hits = sorted(by_path.values(), key=lambda h: ranked.get(h.path, 0), reverse=True)
    result: dict = {"query": query, "hits": [h.model_dump() for h in hits[:limit]]}
    if not index.enabled():
        result["note"] = (
            "semantic search is off (scrive.semanticSearch); keyword matches only"
        )
    elif not index.has_index(site_id):
        result["note"] = (
            "the site's semantic index is being built; keyword matches only for now"
        )
    elif site_id in index.unavailable:
        result["note"] = index.unavailable[site_id]
    return result
