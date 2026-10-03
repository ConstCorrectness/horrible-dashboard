"""Publish to GitHub Pages: a static HTML page in a repository of yours.

Each notebook becomes `<slug>/index.html` (plus the `.ipynb` beside it, which nbviewer
renders as a second link) in the repository named by `notebook.publish.pagesRepo`,
default `<you>/notebooks`, created public if it does not exist.

The repository rules — public only, `.nojekyll`, one commit per publish — live in
`backend/publishing/github_pages.py`, shared with Scrive. The rule that is this
target's own: **the slug is remembered**, not recomputed. It is derived from the file
name the first time; renaming the notebook afterwards must not silently move its
public URL.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import nbformat

from backend.modules.notebook.models import PublicationModel
from backend.modules.notebook.publish.targets.base import (
    PublishContext,
    PublishError,
    PublishResult,
)
from backend.modules.settings.routes import get_value
from backend.publishing import github_pages
from backend.publishing.github_pages import PagesRepo

SETTING = "notebook.publish.pagesRepo"
DEFAULT_REPO = "notebooks"


def slugify(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "notebook"


class PagesTarget:
    id = "pages"
    label = "GitHub Pages"
    visibilities = ("public",)
    can_unpublish = True

    def availability(self) -> tuple[bool, str]:
        return github_pages.availability()

    async def publish(self, ctx: PublishContext) -> PublishResult:
        ok, reason = self.availability()
        if not ok:
            raise PublishError(reason)

        target = await github_pages.resolve_repo(
            str(get_value(SETTING, "") or ""),
            default_repo=DEFAULT_REPO,
            description="Notebooks published from horrible-dashboard",
            setting=SETTING,
        )
        previous = ctx.previous
        slug = str(
            (previous.detail.get("slug") if previous else "") or slugify(ctx.stem)
        )

        html = await asyncio.to_thread(ctx.render_html)
        notebook_file = f"{slug}/{ctx.stem}.ipynb"
        files = {
            f"{slug}/index.html": html.encode("utf-8"),
            notebook_file: nbformat.writes(ctx.notebook).encode("utf-8"),
        }
        stale = (previous.detail.get("files") if previous else None) or []
        await github_pages.push_files(
            target,
            files,
            message=f"Publish {ctx.title}",
            delete=[str(p) for p in stale],
        )
        await github_pages.ensure_pages(target)

        return PublishResult(
            remote_id=f"{target.owner}/{target.repo}/{slug}",
            url=f"{target.site_url}{slug}/",
            extra_url=(
                f"https://nbviewer.org/github/{target.owner}/{target.repo}/blob/"
                f"{target.branch}/{quote(notebook_file)}"
            ),
            note="GitHub Pages takes about a minute to show a change.",
            detail={
                "owner": target.owner,
                "repo": target.repo,
                "branch": target.branch,
                "slug": slug,
                "files": list(files),
            },
        )

    async def unpublish(self, record: PublicationModel) -> None:
        detail = record.detail
        owner, repo = str(detail.get("owner") or ""), str(detail.get("repo") or "")
        if not owner or not repo:
            raise PublishError("This record does not say which repository it lives in.")
        target = PagesRepo(owner, repo, str(detail.get("branch") or "main"))
        await github_pages.push_files(
            target,
            {},
            message=f"Unpublish {detail.get('slug') or record.path}",
            delete=[str(p) for p in detail.get("files") or []],
        )
