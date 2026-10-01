"""arXiv, through `arxiv.client` — which already throttles to arXiv's one request per
three seconds, honours `Retry-After`, and caches. Nothing here talks to arXiv directly.

The default feed is the newest submissions across cs.LG / cs.AI / cs.CL, newest
first: arXiv's own listing order, labelled as exactly that.
"""

from __future__ import annotations

from backend.modules.arxiv import client
from backend.modules.discover.models import (
    DiscoverDetail,
    DiscoverItem,
    Fact,
    FilterSpec,
    KindSpec,
    Link,
    Option,
    SourceSpec,
)
from backend.modules.discover.sources.base import (
    PAGE_SIZE,
    Query,
    SourceResult,
    SourceUnavailable,
    clip,
)

#: The default feed's categories — machine learning, AI, computation & language.
DEFAULT_CATEGORIES = ["cs.LG", "cs.AI", "cs.CL"]

_CATEGORIES = [
    ("", "cs.LG · cs.AI · cs.CL"),
    ("cs.LG", "cs.LG — Machine learning"),
    ("cs.AI", "cs.AI — Artificial intelligence"),
    ("cs.CL", "cs.CL — Computation & language"),
    ("cs.CV", "cs.CV — Computer vision"),
    ("cs.RO", "cs.RO — Robotics"),
    ("cs.SE", "cs.SE — Software engineering"),
    ("cs.CR", "cs.CR — Cryptography & security"),
    ("cs.MA", "cs.MA — Multi-agent systems"),
    ("stat.ML", "stat.ML — Statistics ML"),
    ("math.OC", "math.OC — Optimization & control"),
    ("quant-ph", "quant-ph — Quantum physics"),
]
_SORTS = {
    "submittedDate": "Newest",
    "relevance": "Relevance",
    "lastUpdatedDate": "Recently updated",
}


def to_item(entry: client.ArxivEntry) -> DiscoverItem:
    authors = entry.authors
    author_line = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    facts = [
        Fact(label="authors", value=", ".join(authors)),
        Fact(label="categories", value=" ".join(entry.categories)),
    ]
    if entry.comment:
        facts.append(Fact(label="comment", value=entry.comment))
    if entry.doi:
        facts.append(Fact(label="DOI", value=entry.doi))
    return DiscoverItem(
        source="arxiv",
        kind="paper",
        id=entry.id,
        title=entry.title,
        subtitle=f"{author_line} · {' '.join(entry.categories[:3])}",
        description=clip(entry.summary, 400),
        url=entry.abs_url,
        author=authors[0] if authors else None,
        created_at=entry.published or None,
        updated_at=entry.updated or None,
        tags=entry.categories[:4],
        facts=facts,
    )


class ArxivSource:
    id = "arxiv"

    async def spec(self) -> SourceSpec:
        return SourceSpec(
            id=self.id,
            label="arXiv",
            kinds=[
                KindSpec(
                    id="paper",
                    label="Papers",
                    sorts=[Option(value=k, label=v) for k, v in _SORTS.items()],
                    default_sort="submittedDate",
                    filters=[
                        FilterSpec(
                            id="category",
                            label="Category",
                            options=[
                                Option(value=v, label=label) for v, label in _CATEGORIES
                            ],
                        )
                    ],
                    search_placeholder='Search arXiv — terms, "phrases", au:name…',
                )
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        sort = query.sort if query.sort in _SORTS else "submittedDate"
        category = query.filter("category")
        try:
            start = max(0, int(query.cursor or 0))
        except ValueError:
            start = 0
        try:
            total, entries = await client.search(
                query.q,
                start=start,
                max_results=PAGE_SIZE,
                category=category or None,
                categories=None if category else DEFAULT_CATEGORIES,
                sort=sort,
            )
        except client.ArxivRateLimited as exc:
            raise SourceUnavailable(
                "arXiv is rate-limiting this node.",
                status="rate_limited",
                retry_after=exc.retry_after,
            ) from exc
        except client.ArxivError as exc:
            raise SourceUnavailable(str(exc)) from exc
        where = category or " · ".join(DEFAULT_CATEGORIES)
        label = (
            f"arXiv matches for “{query.q}” in {where} · {_SORTS[sort].lower()}"
            if query.q
            else f"{_SORTS[sort]} submissions in {where}"
        )
        next_start = start + len(entries)
        return SourceResult(
            items=[to_item(e) for e in entries],
            feed_label=label,
            total=total,
            cursor_next=str(next_start) if entries and next_start < total else None,
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        try:
            entry = await client.get_paper(item_id)
        except client.ArxivError as exc:
            raise SourceUnavailable(
                str(exc),
                status="rate_limited"
                if isinstance(exc, client.ArxivRateLimited)
                else "error",
            ) from exc
        item = to_item(entry)
        facts = [Fact(label="arXiv id", value=entry.id), *item.facts]
        if entry.published:
            facts.append(Fact(label="submitted", value=entry.published[:10]))
        if entry.updated and entry.updated != entry.published:
            facts.append(Fact(label="updated", value=entry.updated[:10]))
        return DiscoverDetail(
            item=item,
            body=entry.summary,
            body_format="text",
            facts=facts,
            links=[
                Link(label="arXiv abstract", url=entry.abs_url),
                Link(label="PDF", url=entry.pdf_url),
                Link(
                    label="HF discussion",
                    url=f"https://huggingface.co/papers/{entry.id}",
                ),
            ],
        )
