"""Hugging Face Daily Papers — the community-curated, upvoted feed of new arXiv papers.

Over the same Hub client as models (`huggingface_tools.hub_get`); the endpoints are
keyless. The briefing module reads this feed too, but it wants "the week's top
eight, clipped for a glance"; Discover wants one whole day, unclipped, and search
across every paper the Hub has indexed — different questions of the same API.

A paper's id *is* its arXiv id, so the detail view links straight into the arXiv
save-to-library flow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.modules.connectors.providers import huggingface_tools
from backend.modules.discover.models import (
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
from backend.modules.discover.sources.base import (
    Query,
    SourceResult,
    SourceUnavailable,
    clip,
    num,
)

_SORTS = {
    "upvotes": "Most upvoted",
    "comments": "Most discussed",
    "newest": "Newest submission",
}


def _dates(today: datetime) -> list[Option]:
    out = [Option(value="", label="Latest day")]
    for back in range(1, 8):
        day = today - timedelta(days=back)
        out.append(
            Option(value=day.strftime("%Y-%m-%d"), label=day.strftime("%a %d %b"))
        )
    return out


def to_item(row: dict[str, Any]) -> DiscoverItem | None:
    paper = row.get("paper") or {}
    arxiv_id = str(paper.get("id") or "")
    title = " ".join(str(row.get("title") or paper.get("title") or "").split())
    if not arxiv_id or not title:
        return None
    authors = [
        str(a.get("name"))
        for a in paper.get("authors") or []
        if isinstance(a, dict) and a.get("name") and not a.get("hidden")
    ]
    org = paper.get("organization") or row.get("organization") or {}
    org_name = (
        (org.get("fullname") or org.get("name")) if isinstance(org, dict) else None
    )
    repo = paper.get("githubRepo")
    metrics = [
        Metric(key="upvotes", label="upvotes", value=num(paper.get("upvotes"))),
        Metric(key="comments", label="comments", value=num(row.get("numComments"))),
    ]
    if isinstance(repo, str) and repo:
        metrics.append(
            Metric(
                key="stars", label="GitHub stars", value=num(paper.get("githubStars"))
            )
        )
    author_line = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    return DiscoverItem(
        source="papers",
        kind="paper",
        id=arxiv_id,
        title=title,
        subtitle=" · ".join(x for x in (org_name, author_line) if x),
        description=clip(row.get("summary") or paper.get("summary"), 400),
        url=f"https://huggingface.co/papers/{arxiv_id}",
        author=org_name or (authors[0] if authors else None),
        created_at=paper.get("publishedAt"),
        updated_at=paper.get("submittedOnDailyAt") or row.get("publishedAt"),
        thumbnail=row.get("thumbnail")
        if isinstance(row.get("thumbnail"), str)
        else None,
        metrics=metrics,
        facts=[Fact(label="authors", value=", ".join(authors))] if authors else [],
    )


def _sort(items: list[DiscoverItem], sort: str) -> list[DiscoverItem]:
    def metric(item: DiscoverItem, key: str) -> float:
        return next((m.value or 0.0 for m in item.metrics if m.key == key), 0.0)

    if sort == "comments":
        return sorted(items, key=lambda i: metric(i, "comments"), reverse=True)
    if sort == "newest":
        return sorted(items, key=lambda i: i.created_at or "", reverse=True)
    return sorted(items, key=lambda i: metric(i, "upvotes"), reverse=True)


def _check(data: Any) -> list[Any]:
    if isinstance(data, dict) and data.get("error"):
        raise SourceUnavailable(str(data["error"]))
    return data if isinstance(data, list) else []


class PapersSource:
    id = "papers"

    async def spec(self) -> SourceSpec:
        today = datetime.now(timezone.utc)
        return SourceSpec(
            id=self.id,
            label="HF Papers",
            kinds=[
                KindSpec(
                    id="paper",
                    label="Daily Papers",
                    sorts=[Option(value=k, label=v) for k, v in _SORTS.items()],
                    default_sort="upvotes",
                    filters=[FilterSpec(id="date", label="Day", options=_dates(today))],
                    search_placeholder="Search every paper on the Hub…",
                )
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        sort = query.sort if query.sort in _SORTS else "upvotes"
        if query.q:
            data, _ = await huggingface_tools.hub_get(
                "/papers/search", params={"q": query.q, "limit": 50}
            )
            items = [i for i in map(to_item, _check(data)) if i]
            # Search is relevance-ranked upstream; only re-sort when asked to.
            if query.sort:
                items = _sort(items, sort)
            return SourceResult(
                items=items, feed_label=f"Hugging Face papers matching “{query.q}”"
            )

        params: dict[str, Any] = {"limit": 100}
        if day := query.filter("date"):
            params["date"] = day
        data, _ = await huggingface_tools.hub_get("/daily_papers", params=params)
        items = [i for i in map(to_item, _check(data)) if i]
        if not day:
            # Undated, the endpoint returns the newest 100 *across days* (58 from
            # today and 42 from yesterday, say). "Latest day" means one day, so
            # keep only the newest date present.
            day = max(((i.updated_at or "")[:10] for i in items), default="")
            items = [i for i in items if (i.updated_at or "").startswith(day)]
        items = _sort(items, sort)
        label = f"HF Daily Papers · {day or 'latest'} · {_SORTS[sort].lower()}"
        return SourceResult(items=items, feed_label=label)

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        data, _ = await huggingface_tools.hub_get(f"/papers/{item_id}")
        if isinstance(data, dict) and data.get("error"):
            if known is None:
                raise SourceUnavailable(str(data["error"]))
            paper: dict[str, Any] = {}
        else:
            paper = data if isinstance(data, dict) else {}
        item = known or to_item({"paper": paper, "title": paper.get("title")})
        if item is None:
            raise SourceUnavailable(f"paper {item_id} not found")
        summary = paper.get("summary") or item.description
        facts = list(item.facts)
        facts.append(Fact(label="arXiv id", value=item_id))
        if paper.get("publishedAt"):
            facts.append(Fact(label="published", value=str(paper["publishedAt"])[:10]))
        if ai := paper.get("ai_summary"):
            facts.append(Fact(label="HF AI summary", value=clip(ai, 400)))
        links = [
            Link(
                label="Discussion on HF", url=f"https://huggingface.co/papers/{item_id}"
            ),
            Link(label="arXiv abstract", url=f"https://arxiv.org/abs/{item_id}"),
            Link(label="PDF", url=f"https://arxiv.org/pdf/{item_id}"),
        ]
        if isinstance(paper.get("githubRepo"), str) and paper["githubRepo"]:
            links.append(Link(label="Code", url=paper["githubRepo"]))
        if isinstance(paper.get("projectPage"), str) and paper["projectPage"]:
            links.append(Link(label="Project page", url=paper["projectPage"]))
        return DiscoverDetail(
            item=item, body=summary, body_format="text", facts=facts, links=links
        )
