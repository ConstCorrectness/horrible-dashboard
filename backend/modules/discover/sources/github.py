"""GitHub repositories, through `github_api.public_get` — authenticated when the
connector is set up, anonymous otherwise.

GitHub has **no trending API**. The default feed is therefore a search whose label
says precisely what it is ("Most-starred machine-learning repos pushed in the last 7
days"), built from the same filters the user can see and change.

The search API allows 10 requests a minute anonymously (30 with a token) and reports
the window in `X-RateLimit-*`. A rate-limited answer carries the reset time so the
pane can say "resets in 41s" instead of "error".
"""

from __future__ import annotations

import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.modules.connectors.providers import github, github_api
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
    PAGE_SIZE,
    Query,
    SourceResult,
    SourceUnavailable,
    clip,
    header,
    num,
)

#: GitHub's search API stops at the 1000th result whatever `total_count` says.
SEARCH_CAP = 1000

_SORTS = {
    "stars": "Most-starred",
    "updated": "Recently updated",
    "forks": "Most-forked",
}
_TOPICS = [
    ("machine-learning", "machine-learning"),
    ("llm", "llm"),
    ("deep-learning", "deep-learning"),
    ("ai-agents", "ai-agents"),
    ("reinforcement-learning", "reinforcement-learning"),
    ("computer-vision", "computer-vision"),
    ("nlp", "nlp"),
    ("mcp", "mcp"),
    ("", "Any topic"),
]
_LANGUAGES = [
    ("", "Any language"),
    ("Python", "Python"),
    ("TypeScript", "TypeScript"),
    ("Rust", "Rust"),
    ("C++", "C++"),
    ("Go", "Go"),
    ("Jupyter Notebook", "Jupyter Notebook"),
    ("CUDA", "CUDA"),
]
_WINDOWS = {
    "pushed7": ("Pushed in the last 7 days", "pushed", 7),
    "pushed30": ("Pushed in the last 30 days", "pushed", 30),
    "created30": ("Created in the last 30 days", "created", 30),
    "created365": ("Created in the last year", "created", 365),
    "": ("Any time", "", 0),
}


def build_q(query: Query, today: datetime | None = None) -> tuple[str, str]:
    """`(GitHub search q, human description of the qualifiers)`."""
    today = today or datetime.now(timezone.utc)
    parts: list[str] = []
    said: list[str] = []
    if query.q:
        parts.append(query.q)
    topic = query.filter("topic", "" if query.q else "machine-learning")
    if topic:
        parts.append(f"topic:{topic}")
        said.append(topic)
    language = query.filter("language")
    if language:
        parts.append(f'language:"{language}"')
        said.append(language)
    window = query.filter("window", "" if query.q else "pushed7")
    label, field, days = _WINDOWS.get(window, _WINDOWS[""])
    if field:
        since = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        parts.append(f"{field}:>{since}")
    if not query.q:
        # A browse with no terms would otherwise surface every one-star fork.
        parts.append("stars:>100")
    qualifier = " ".join(said)
    return " ".join(parts), (
        f"{qualifier} repos · {label.lower()}" if field else f"{qualifier} repos"
    ).strip()


def to_item(repo: dict[str, Any]) -> DiscoverItem:
    full = str(repo.get("full_name") or "")
    badges: list[Badge] = []
    if repo.get("archived"):
        badges.append(Badge(label="archived", tone="warn"))
    if repo.get("fork"):
        badges.append(Badge(label="fork"))
    if repo.get("private"):
        badges.append(Badge(label="private", tone="info"))
    lic = (repo.get("license") or {}).get("spdx_id")
    if lic and lic != "NOASSERTION":
        badges.append(Badge(label=str(lic)))
    owner = repo.get("owner") or {}
    return DiscoverItem(
        source="github",
        kind="repo",
        id=full,
        title=full,
        subtitle=" · ".join(x for x in (repo.get("language"),) if x),
        description=clip(repo.get("description"), 300),
        url=repo.get("html_url"),
        author=owner.get("login"),
        created_at=repo.get("created_at"),
        updated_at=repo.get("pushed_at") or repo.get("updated_at"),
        thumbnail=owner.get("avatar_url"),
        tags=[t for t in repo.get("topics") or [] if isinstance(t, str)][:8],
        badges=badges,
        metrics=[
            Metric(key="stars", label="stars", value=num(repo.get("stargazers_count"))),
            Metric(key="forks", label="forks", value=num(repo.get("forks_count"))),
            Metric(
                key="issues",
                label="open issues + PRs",
                value=num(repo.get("open_issues_count")),
            ),
        ],
    )


def raise_for(data: Any, headers: dict[str, str], *, connected: bool = True) -> None:
    if not (isinstance(data, dict) and data.get("error")):
        return
    if data.get("status") == 429:
        reset = num(header(headers, "x-ratelimit-reset"))
        retry = (
            max(0.0, reset - time.time()) if reset else num(header(headers, "retry-after"))
        )
        raise SourceUnavailable(
            "GitHub's rate limit is used up"
            + (" — connecting GitHub raises it." if not connected else "."),
            status="rate_limited",
            retry_after=retry,
        )
    raise SourceUnavailable(str(data["error"]))


class GitHubSource:
    id = "github"

    async def spec(self) -> SourceSpec:
        connected = bool(await github.token())
        return SourceSpec(
            id=self.id,
            label="GitHub",
            connected=connected,
            auth_hint=None
            if connected
            else "Anonymous: 10 searches a minute. Connect GitHub for 30 and your private repos.",
            kinds=[
                KindSpec(
                    id="repo",
                    label="Repositories",
                    sorts=[Option(value=k, label=v) for k, v in _SORTS.items()],
                    default_sort="stars",
                    filters=[
                        FilterSpec(
                            id="topic",
                            label="Topic",
                            options=[
                                Option(value=v, label=label) for v, label in _TOPICS
                            ],
                            default="machine-learning",
                        ),
                        FilterSpec(
                            id="language",
                            label="Language",
                            options=[
                                Option(value=v, label=label) for v, label in _LANGUAGES
                            ],
                        ),
                        FilterSpec(
                            id="window",
                            label="When",
                            options=[
                                Option(value=k, label=v[0]) for k, v in _WINDOWS.items()
                            ],
                            default="pushed7",
                        ),
                    ],
                    search_placeholder="Search repositories — name, description, readme…",
                )
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        sort = query.sort if query.sort in _SORTS else "stars"
        q, described = build_q(query)
        try:
            page = max(1, int(query.cursor or 1))
        except ValueError:
            page = 1
        data, headers = await github_api.public_get(
            "/search/repositories",
            params={
                "q": q,
                "sort": sort,
                "order": "desc",
                "per_page": PAGE_SIZE,
                "page": page,
            },
        )
        raise_for(data, headers, connected=bool(await github.token()))
        body = data if isinstance(data, dict) else {}
        total = int(body.get("total_count") or 0)
        items = [to_item(r) for r in body.get("items") or [] if isinstance(r, dict)]
        reachable = min(total, SEARCH_CAP)
        label = (
            f"{_SORTS[sort]} repos matching “{query.q}”"
            + (f" · {described}" if described != "repos" else "")
            if query.q
            else f"{_SORTS[sort]} {described}"
        )
        return SourceResult(
            items=items,
            feed_label=label,
            total=total,
            cursor_next=str(page + 1)
            if items and page * PAGE_SIZE < reachable
            else None,
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        if item_id.count("/") != 1:
            raise SourceUnavailable(f"not an owner/name repo id: {item_id}")
        data, headers = await github_api.public_get(f"/repos/{item_id}")
        raise_for(data, headers)
        repo = data if isinstance(data, dict) else {}
        item = to_item(repo)
        readme_body: str | None = None
        readme, _ = await github_api.public_get(f"/repos/{item_id}/readme")
        if isinstance(readme, dict) and readme.get("content"):
            try:
                readme_body = base64.b64decode(readme["content"]).decode(
                    "utf-8", "replace"
                )
            except ValueError:
                readme_body = None
        facts = [
            Fact(label=label, value=str(value))
            for label, value in (
                ("watchers", repo.get("subscribers_count")),
                ("default branch", repo.get("default_branch")),
                ("license", (repo.get("license") or {}).get("name")),
                ("created", (repo.get("created_at") or "")[:10]),
                ("last push", (repo.get("pushed_at") or "")[:10]),
                (
                    "size",
                    f"{int(repo['size']) / 1024:.1f} MB" if repo.get("size") else None,
                ),
            )
            if value
        ]
        links = [
            Link(
                label="Open on GitHub", url=item.url or f"https://github.com/{item_id}"
            )
        ]
        if homepage := repo.get("homepage"):
            links.append(Link(label="Homepage", url=str(homepage)))
        return DiscoverDetail(item=item, body=readme_body, facts=facts, links=links)
