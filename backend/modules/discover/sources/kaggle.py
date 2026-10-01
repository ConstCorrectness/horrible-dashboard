"""Kaggle: competitions, datasets, models — and an honest answer about benchmarks.

Through the training module's Kaggle provider (`kaggle_provider.client()`), which owns
credential staging. Every Kaggle API call needs credentials, even for public
listings, so without them this source reports `needs_connect` and lists nothing: no
scraping kaggle.com as a fallback, because a scraper breaks silently and a browse
surface that quietly shows stale or wrong rows is worse than one that says why it's
empty.

The `kaggle` client is synchronous, so each call runs in a worker thread.

**Benchmarks.** The installed SDK can fetch the leaderboard of a benchmark you
already know the slug of, but has no call that *lists* benchmarks. Rather than invent
a list, the benchmark kind reports `degraded` with a link to the site.
"""

from __future__ import annotations

import asyncio
from typing import Any

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
from backend.modules.discover.sources.base import (
    Query,
    SourceResult,
    SourceUnavailable,
    clip,
    iso,
    num,
)

AUTH_HINT = (
    "Kaggle's API needs credentials for every call. Set training.kaggle.username and "
    "training.kaggle.key in Settings, or put kaggle.json in ~/.kaggle."
)

_COMP_SORTS = {
    "recentlyCreated": "Newest",
    "prize": "Largest prize",
    "earliestDeadline": "Closing soonest",
    "numberOfTeams": "Most teams",
    "grouped": "Kaggle's default order",
}
_COMP_CATEGORIES = [
    ("", "All categories"),
    ("featured", "Featured"),
    ("research", "Research"),
    ("playground", "Playground"),
    ("gettingStarted", "Getting started"),
    ("recruitment", "Recruitment"),
]
_DATASET_SORTS = {
    "hottest": "Hottest",
    "votes": "Most votes",
    "updated": "Recently updated",
    "published": "Newest",
}
_DATASET_TYPES = [
    ("", "Any file type"),
    ("csv", "CSV"),
    ("json", "JSON"),
    ("parquet", "Parquet"),
    ("sqlite", "SQLite"),
]
_MODEL_SORTS = {
    "hotness": "Hottest",
    "downloadCount": "Most downloaded",
    "voteCount": "Most votes",
    "createTime": "Newest",
}
#: `model_list` prints its next-page token instead of returning it, so models are
#: fetched as one larger page with no "load more".
MODEL_PAGE = 50


def _tag_names(tags: Any) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        name = getattr(tag, "name", None) or (
            tag.get("name") if isinstance(tag, dict) else None
        )
        if name:
            out.append(str(name))
    return out[:8]


def competition_item(c: Any) -> DiscoverItem:
    ref = str(getattr(c, "ref", "") or "")
    slug = ref.rstrip("/").rsplit("/", 1)[-1]
    reward = str(getattr(c, "reward", "") or "")
    deadline = iso(getattr(c, "deadline", None))
    badges = []
    if reward:
        badges.append(
            Badge(
                label=reward,
                tone="ok" if reward.startswith(("$", "€", "£")) else "idle",
            )
        )
    if category := getattr(c, "category", None):
        badges.append(Badge(label=str(category)))
    if getattr(c, "user_has_entered", False):
        badges.append(Badge(label="entered", tone="info"))
    facts = [
        Fact(label=label, value=str(value))
        for label, value in (
            ("evaluation metric", getattr(c, "evaluation_metric", None)),
            ("deadline", (deadline or "")[:16].replace("T", " ")),
            ("reward", reward),
            (
                "host",
                getattr(c, "organization_name", None) or getattr(c, "host_name", None),
            ),
            ("max team size", getattr(c, "max_team_size", None)),
            ("max daily submissions", getattr(c, "max_daily_submissions", None)),
        )
        if value
    ]
    return DiscoverItem(
        source="kaggle",
        kind="competition",
        id=slug,
        title=str(getattr(c, "title", "") or slug),
        subtitle=" · ".join(
            x
            for x in (
                str(getattr(c, "evaluation_metric", "") or ""),
                f"closes {deadline[:10]}" if deadline else "",
            )
            if x
        ),
        description=clip(getattr(c, "description", ""), 400),
        url=f"https://www.kaggle.com/competitions/{slug}",
        author=getattr(c, "organization_name", None) or getattr(c, "host_name", None),
        created_at=iso(getattr(c, "enabled_date", None)),
        # No `updated_at`: the API has no last-modified date for a competition, and
        # the deadline (already in the subtitle and facts) is not one.
        updated_at=None,
        thumbnail=getattr(c, "thumbnail_image_url", None) or None,
        tags=_tag_names(getattr(c, "tags", None)),
        badges=badges,
        # No notebook count: the list API returns `kernel_count=0` for every
        # competition (Titanic included), so showing it would be a confident lie.
        metrics=[
            Metric(
                key="teams", label="teams", value=num(getattr(c, "team_count", None))
            ),
        ],
        facts=facts,
    )


def dataset_item(d: Any) -> DiscoverItem:
    ref = str(getattr(d, "ref", "") or "")
    lic = getattr(d, "license_name", None)
    return DiscoverItem(
        source="kaggle",
        kind="dataset",
        id=ref,
        title=str(getattr(d, "title", "") or ref),
        subtitle=ref,
        description=clip(
            getattr(d, "subtitle", "") or getattr(d, "description", ""), 300
        ),
        url=f"https://www.kaggle.com/datasets/{ref}",
        author=getattr(d, "creator_name", None) or getattr(d, "owner_name", None),
        updated_at=iso(getattr(d, "last_updated", None)),
        thumbnail=getattr(d, "thumbnail_image_url", None) or None,
        tags=_tag_names(getattr(d, "tags", None)),
        badges=[Badge(label=str(lic))] if lic else [],
        metrics=[
            Metric(
                key="downloads",
                label="downloads",
                value=num(getattr(d, "download_count", None)),
            ),
            Metric(
                key="votes", label="votes", value=num(getattr(d, "vote_count", None))
            ),
            Metric(
                key="usability",
                label="usability",
                value=num(getattr(d, "usability_rating", None)),
                unit="percent",
            ),
            Metric(
                key="size",
                label="size",
                value=num(getattr(d, "total_bytes", None)),
                unit="bytes",
            ),
        ],
        facts=[
            Fact(label=label, value=str(value))
            for label, value in (
                ("license", lic),
                ("views", getattr(d, "view_count", None)),
                ("notebooks", getattr(d, "kernel_count", None)),
                ("version", getattr(d, "current_version_number", None)),
            )
            if value
        ],
    )


def _framework_name(value: Any) -> str:
    """`ModelFramework.MODEL_FRAMEWORK_PY_TORCH` (enum or its string) → `py torch`."""
    if value is None:
        return ""
    raw = getattr(value, "name", None) or str(value)
    raw = raw.rsplit(".", 1)[-1].replace("MODEL_FRAMEWORK_", "")
    return {
        "PY_TORCH": "PyTorch",
        "TENSOR_FLOW_2": "TensorFlow 2",
        "TENSOR_FLOW_1": "TensorFlow 1",
    }.get(raw, raw.replace("_", " ").lower())


def model_item(m: Any) -> DiscoverItem:
    ref = str(getattr(m, "ref", "") or "")
    frameworks = sorted(
        {
            _framework_name(
                getattr(i, "framework", None)
                or (i.get("framework") if isinstance(i, dict) else None)
            )
            for i in getattr(m, "instances", None) or []
        }
        - {""}
    )
    return DiscoverItem(
        source="kaggle",
        kind="model",
        id=ref,
        title=str(getattr(m, "title", "") or ref),
        subtitle=" · ".join([ref, *frameworks]),
        description=clip(getattr(m, "subtitle", ""), 300),
        url=getattr(m, "url", None) or f"https://www.kaggle.com/models/{ref}",
        author=getattr(m, "author", None),
        created_at=iso(getattr(m, "publish_time", None)),
        updated_at=iso(getattr(m, "update_time", None)),
        tags=_tag_names(getattr(m, "tags", None)),
        metrics=[
            Metric(
                key="votes", label="votes", value=num(getattr(m, "vote_count", None))
            )
        ],
        facts=[Fact(label="frameworks", value=", ".join(frameworks))]
        if frameworks
        else [],
    )


def _client() -> Any:
    from backend.modules.training.providers import kaggle_provider  # noqa: PLC0415
    from backend.modules.training.providers.base import ProviderError  # noqa: PLC0415

    try:
        return kaggle_provider.client()
    except ProviderError as exc:
        raise SourceUnavailable(f"{AUTH_HINT} ({exc})", status="needs_connect") from exc


def _list_sync(query: Query) -> SourceResult:
    from backend.modules.training.providers import kaggle_provider  # noqa: PLC0415

    api = _client()
    try:
        page = max(1, int(query.cursor or 1))
    except ValueError:
        page = 1
    q = query.q or None
    try:
        if query.kind == "dataset":
            sort = query.sort if query.sort in _DATASET_SORTS else "hottest"
            rows = api.dataset_list(
                sort_by=sort,
                search=q,
                file_type=query.filter("filetype") or None,
                page=page,
            )
            items = [dataset_item(d) for d in rows or [] if d is not None]
            label = f"{_DATASET_SORTS[sort]} Kaggle datasets"
        elif query.kind == "model":
            sort = query.sort if query.sort in _MODEL_SORTS else "hotness"
            rows = api.model_list(sort_by=sort, search=q, page_size=MODEL_PAGE)
            items = [model_item(m) for m in rows or [] if m is not None]
            return SourceResult(
                items=items,
                feed_label=f"{_MODEL_SORTS[sort]} Kaggle models"
                + (f" matching “{query.q}”" if query.q else ""),
            )
        else:
            sort = query.sort if query.sort in _COMP_SORTS else "recentlyCreated"
            if q and not query.sort:
                sort = "relevance"
            result = api.competitions_list(
                sort_by=sort,
                category=query.filter("category") or None,
                search=q,
                page=page,
            )
            rows = kaggle_provider.as_items(result, "competitions")
            items = [competition_item(c) for c in rows if c is not None]
            label = f"{_COMP_SORTS.get(sort, 'Relevant')} active Kaggle competitions"
    except SourceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — the SDK raises whatever its HTTP layer does
        text = str(exc)
        if "401" in text or "403" in text or "Unauthorized" in text:
            raise SourceUnavailable(
                f"{AUTH_HINT} ({text[:160]})", status="needs_connect"
            ) from exc
        if "429" in text:
            raise SourceUnavailable(
                "Kaggle rate limit reached.", status="rate_limited"
            ) from exc
        raise SourceUnavailable(f"Kaggle: {text[:240]}") from exc
    if query.q:
        label += f" matching “{query.q}”"
    # Kaggle returns 20 per page and no total; a full page means there may be more.
    return SourceResult(
        items=items,
        feed_label=label,
        cursor_next=str(page + 1) if len(items) >= 20 else None,
    )


class KaggleSource:
    id = "kaggle"

    async def spec(self) -> SourceSpec:
        from backend.modules.training.providers import kaggle_provider  # noqa: PLC0415

        connected = kaggle_provider.has_credentials()
        return SourceSpec(
            id=self.id,
            label="Kaggle",
            requires_auth=True,
            connected=connected,
            auth_hint=None if connected else AUTH_HINT,
            kinds=[
                KindSpec(
                    id="competition",
                    label="Competitions",
                    sorts=[Option(value=k, label=v) for k, v in _COMP_SORTS.items()],
                    default_sort="recentlyCreated",
                    filters=[
                        FilterSpec(
                            id="category",
                            label="Category",
                            options=[
                                Option(value=v, label=label)
                                for v, label in _COMP_CATEGORIES
                            ],
                        )
                    ],
                    search_placeholder="Search competitions…",
                ),
                KindSpec(
                    id="dataset",
                    label="Datasets",
                    sorts=[Option(value=k, label=v) for k, v in _DATASET_SORTS.items()],
                    default_sort="hottest",
                    filters=[
                        FilterSpec(
                            id="filetype",
                            label="File type",
                            options=[
                                Option(value=v, label=label)
                                for v, label in _DATASET_TYPES
                            ],
                        )
                    ],
                    search_placeholder="Search Kaggle datasets…",
                ),
                KindSpec(
                    id="model",
                    label="Models",
                    sorts=[Option(value=k, label=v) for k, v in _MODEL_SORTS.items()],
                    default_sort="hotness",
                    search_placeholder="Search Kaggle models…",
                ),
                KindSpec(id="benchmark", label="Benchmarks", searchable=False),
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        if query.kind == "benchmark":
            return SourceResult(
                items=[],
                feed_label="Kaggle Benchmarks",
                status="degraded",
                message=(
                    "Kaggle's API has no call that lists benchmarks (only the leaderboard "
                    "of one you already know). Browse them at https://www.kaggle.com/benchmarks."
                ),
            )
        return await asyncio.to_thread(_list_sync, query)

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        if known is None:
            # No cheap single-item endpoint; the list row carries everything shown.
            raise SourceUnavailable(
                "open this item from a list first", status="degraded"
            )
        body = known.description or None
        links = [Link(label="Open on Kaggle", url=known.url or "")]
        if kind == "competition":
            links += [
                Link(label="Leaderboard", url=f"{known.url}/leaderboard"),
                Link(label="Data", url=f"{known.url}/data"),
            ]
        return DiscoverDetail(
            item=known, body=body, body_format="text", facts=known.facts, links=links
        )
