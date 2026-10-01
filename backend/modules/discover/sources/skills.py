"""Agent skills published in a few well-known GitHub repos (`skill_repos.json`).

A skill is a directory holding a `SKILL.md` (YAML front matter `name` +
`description`, then the body) and, often, scripts and reference files beside it.

Cost model, because anonymous GitHub allows 60 core API calls an hour:
- **One** API call per repo: `git/trees/{branch}?recursive=1` lists every path, so
  every `SKILL.md` *and* its sibling files are known without walking directories.
- The `SKILL.md` texts come from `raw.githubusercontent.com`, a CDN outside the API
  rate limit, a few at a time.
- The whole repo listing is cached for hours: these repos change weekly, not by the
  minute.

Installing copies the **whole directory** into the user skills folder, not just
`SKILL.md` — a skill whose body says "run scripts/fill_form.py" is broken without
the script.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from backend.modules.connectors.providers import github_api
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
    front_matter,
    strip_front_matter,
)
from backend.modules.discover.sources.github import raise_for

RAW = "https://raw.githubusercontent.com"
REPOS_FILE = Path(__file__).resolve().parent.parent / "skill_repos.json"
REPO_TTL_S = 6 * 3600.0
#: Install refuses anything bigger: a skill is instructions plus a few helpers, and
#: a directory of model weights is not one.
MAX_FILES = 80
MAX_FILE_BYTES = 2_000_000
_FETCH_CONCURRENCY = 8


@dataclass
class SkillEntry:
    repo: str
    branch: str
    directory: str  # path inside the repo, e.g. "skills/pdf"
    name: str
    description: str
    body: str
    files: list[tuple[str, int]] = field(default_factory=list)  # (rel path, bytes)
    stars: int | None = None

    @property
    def id(self) -> str:
        return f"{self.repo}:{self.directory}"


_repo_cache: dict[str, tuple[float, list[SkillEntry]]] = {}
_repo_locks: dict[str, asyncio.Lock] = {}


def repos() -> list[dict[str, str]]:
    try:
        data = json.loads(REPOS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("repo")]


def installed_names() -> set[str]:
    from backend.modules.skills import store  # noqa: PLC0415 — skills is optional here

    try:
        return {s.name for s in store.list_skills()}
    except Exception:  # noqa: BLE001 — a broken skills dir must not break browsing
        return set()


async def _raw(
    client: httpx.AsyncClient, repo: str, branch: str, path: str
) -> str | None:
    try:
        res = await client.get(f"{RAW}/{repo}/{branch}/{path}")
    except httpx.HTTPError:
        return None
    return res.text if res.status_code == 200 else None


async def load_repo(repo: str, *, fresh: bool = False) -> list[SkillEntry]:
    """Every skill in `repo`, cached for `REPO_TTL_S`."""
    hit = _repo_cache.get(repo)
    if hit and not fresh and time.time() - hit[0] < REPO_TTL_S:
        return hit[1]
    lock = _repo_locks.setdefault(repo, asyncio.Lock())
    async with lock:
        hit = _repo_cache.get(repo)
        if hit and not fresh and time.time() - hit[0] < REPO_TTL_S:
            return hit[1]
        meta, headers = await github_api.public_get(f"/repos/{repo}")
        raise_for(meta, headers)
        branch = str((meta or {}).get("default_branch") or "main")
        stars = (meta or {}).get("stargazers_count")
        tree, headers = await github_api.public_get(
            f"/repos/{repo}/git/trees/{branch}", params={"recursive": "1"}
        )
        raise_for(tree, headers)
        blobs = [
            (str(t["path"]), int(t.get("size") or 0))
            for t in (tree or {}).get("tree") or []
            if isinstance(t, dict) and t.get("type") == "blob" and t.get("path")
        ]
        skill_dirs = sorted(
            p.rsplit("/", 1)[0] if "/" in p else ""
            for p, _ in blobs
            if p == "SKILL.md" or p.endswith("/SKILL.md")
        )
        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:

            async def one(directory: str) -> SkillEntry | None:
                path = f"{directory}/SKILL.md" if directory else "SKILL.md"
                async with sem:
                    text = await _raw(client, repo, branch, path)
                if text is None:
                    return None
                fm = front_matter(text)
                name = fm.get("name") or directory.rsplit("/", 1)[-1] or repo
                prefix = f"{directory}/" if directory else ""
                files = [
                    (p[len(prefix) :], size)
                    for p, size in blobs
                    if p.startswith(prefix)
                    and p != path
                    # A nested skill's files belong to it, not to this one.
                    and not any(
                        p.startswith(f"{d}/")
                        for d in skill_dirs
                        if d != directory and d.startswith(prefix)
                    )
                ]
                return SkillEntry(
                    repo=repo,
                    branch=branch,
                    directory=directory,
                    name=name,
                    description=fm.get("description", ""),
                    body=strip_front_matter(text),
                    files=files,
                    stars=stars if isinstance(stars, int) else None,
                )

            results = await asyncio.gather(*(one(d) for d in skill_dirs))
        # A top-level SKILL.md beside a skills/ folder is the repo's own (templates,
        # meta-skills); keep it, it's real.
        entries = [e for e in results if e is not None and e.description]
        _repo_cache[repo] = (time.time(), entries)
        return entries


def to_item(entry: SkillEntry, label: str, installed: set[str]) -> DiscoverItem:
    badges = [Badge(label=label)]
    if entry.name in installed:
        badges.insert(0, Badge(label="installed", tone="ok"))
    if entry.files:
        badges.append(
            Badge(
                label=f"+{len(entry.files)} files",
                title="Scripts and references beside SKILL.md",
            )
        )
    return DiscoverItem(
        source="skills",
        kind="skill",
        id=entry.id,
        title=entry.name,
        subtitle=f"{entry.repo}/{entry.directory}".rstrip("/"),
        description=clip(entry.description, 400),
        url=f"https://github.com/{entry.repo}/tree/{entry.branch}/{entry.directory}".rstrip(
            "/"
        ),
        author=entry.repo.split("/", 1)[0],
        badges=badges,
        metrics=[Metric(key="stars", label="repo stars", value=entry.stars)],
    )


def _split_id(item_id: str) -> tuple[str, str]:
    repo, _, directory = item_id.partition(":")
    if repo.count("/") != 1:
        raise SourceUnavailable(f"not a skill id: {item_id}")
    return repo, directory


async def find(item_id: str) -> SkillEntry:
    repo, directory = _split_id(item_id)
    if repo not in {r["repo"] for r in repos()}:
        raise SourceUnavailable(f"{repo} is not one of the listed skill repos")
    for entry in await load_repo(repo):
        if entry.directory == directory:
            return entry
    raise SourceUnavailable(f"no skill at {item_id}")


async def install(item_id: str) -> tuple[str, int]:
    """Copy a listed skill's directory into the user skills folder.

    Returns `(skill name, files written)`. Refuses to overwrite an existing skill of
    the same name — that is someone's edited copy, and clobbering it is not a
    browse surface's call.
    """
    from backend.modules.skills import store  # noqa: PLC0415

    entry = await find(item_id)
    if problem := store.validate_name(entry.name):
        raise SourceUnavailable(problem)
    if len(entry.files) + 1 > MAX_FILES:
        raise SourceUnavailable(
            f"{entry.name} has {len(entry.files) + 1} files; the limit is {MAX_FILES}"
        )
    if any(size > MAX_FILE_BYTES for _, size in entry.files):
        raise SourceUnavailable(
            f"{entry.name} contains a file over {MAX_FILE_BYTES // 1_000_000} MB"
        )
    target = store.user_dir() / entry.name
    if target.exists():
        raise SourceUnavailable(
            f"a skill named {entry.name} already exists — remove it first"
        )

    prefix = f"{entry.directory}/" if entry.directory else ""
    rels = ["SKILL.md", *(rel for rel, _ in entry.files)]
    payloads: dict[str, bytes] = {}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for rel in rels:
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise SourceUnavailable(f"refusing unsafe path {rel}")
            try:
                res = await client.get(
                    f"{RAW}/{entry.repo}/{entry.branch}/{prefix}{rel}"
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"couldn't download {rel}: {exc}") from exc
            if res.status_code != 200:
                raise SourceUnavailable(
                    f"couldn't download {rel}: HTTP {res.status_code}"
                )
            payloads[rel] = res.content

    # All downloaded before anything is written: a half-installed skill is worse
    # than none.
    staging = target.with_name(f".{entry.name}.installing")
    try:
        for rel, data in payloads.items():
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        staging.rename(target)
    except OSError as exc:
        raise SourceUnavailable(f"couldn't write the skill: {exc}") from exc
    finally:
        if staging.exists():
            import shutil  # noqa: PLC0415

            shutil.rmtree(staging, ignore_errors=True)
    return entry.name, len(payloads)


class SkillsSource:
    id = "skills"

    async def spec(self) -> SourceSpec:
        listed = repos()
        return SourceSpec(
            id=self.id,
            label="Agent skills",
            kinds=[
                KindSpec(
                    id="skill",
                    label="Skills",
                    sorts=[
                        Option(value="name", label="Name"),
                        Option(value="stars", label="Repo stars"),
                    ],
                    default_sort="name",
                    filters=[
                        FilterSpec(
                            id="repo",
                            label="Publisher",
                            options=[Option(value="", label="All publishers")]
                            + [
                                Option(
                                    value=r["repo"], label=r.get("label") or r["repo"]
                                )
                                for r in listed
                            ],
                        )
                    ],
                    search_placeholder="Filter skills by name or description…",
                )
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        listed = repos()
        chosen = query.filter("repo")
        targets = [r for r in listed if not chosen or r["repo"] == chosen]
        results = await asyncio.gather(
            *(load_repo(r["repo"]) for r in targets), return_exceptions=True
        )
        installed = installed_names()
        items: list[DiscoverItem] = []
        failures: list[str] = []
        rate_limited: SourceUnavailable | None = None
        for repo, result in zip(targets, results):
            if isinstance(result, BaseException):
                failures.append(repo["repo"])
                if (
                    isinstance(result, SourceUnavailable)
                    and result.status == "rate_limited"
                ):
                    rate_limited = result
                continue
            items += [
                to_item(e, repo.get("label") or repo["repo"], installed) for e in result
            ]
        if not items and rate_limited:
            raise rate_limited
        if not items and failures:
            raise SourceUnavailable(f"couldn't read {', '.join(failures)}")
        if needle := query.q.lower():
            items = [
                i
                for i in items
                if needle in i.title.lower() or needle in i.description.lower()
            ]
        if query.sort == "stars":
            items.sort(key=lambda i: -(i.metrics[0].value or 0))
        else:
            items.sort(key=lambda i: i.title.lower())
        where = chosen or ", ".join(r["repo"] for r in listed)
        return SourceResult(
            items=items,
            feed_label=f"Skills published in {where}"
            + (f" matching “{query.q}”" if query.q else ""),
            total=len(items),
            status="degraded" if failures else "ok",
            message=f"Couldn't read {', '.join(failures)}." if failures else None,
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        entry = await find(item_id)
        label = next(
            (r.get("label") for r in repos() if r["repo"] == entry.repo), entry.repo
        )
        item = known or to_item(entry, label or entry.repo, installed_names())
        facts = [
            Fact(label="skill name", value=entry.name),
            Fact(label="repository", value=entry.repo),
            Fact(label="path", value=entry.directory or "/"),
            Fact(label="files", value=str(len(entry.files) + 1)),
        ]
        return DiscoverDetail(
            item=item,
            body=entry.body,
            facts=facts,
            files=["SKILL.md", *(rel for rel, _ in entry.files)],
            links=[Link(label="Open on GitHub", url=item.url or "")],
        )
