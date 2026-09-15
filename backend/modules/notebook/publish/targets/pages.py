"""Publish to GitHub Pages: a static HTML page in a repository of yours.

Each notebook becomes `<slug>/index.html` (plus the `.ipynb` beside it, which nbviewer
renders as a second link) in the repository named by `notebook.publish.pagesRepo`,
default `<you>/notebooks`, created public if it does not exist.

Three rules that are quiet if wrong:

- **Always public.** Pages on a private repository needs a paid plan, so a private repo
  yields a link that 404s for everyone. That is refused up front rather than published.
- **`.nojekyll` goes in first.** Without it Pages runs Jekyll over the repository, which
  drops any path starting with `_` and adds a minute to every publish for nothing.
- **The slug is remembered**, not recomputed. It is derived from the file name the first
  time; renaming the notebook afterwards must not silently move its public URL.

Pages builds asynchronously — the first publish takes a minute or so to appear — and the
result note says so, because a fresh link that 404s looks exactly like a broken one.
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any
from urllib.parse import quote

import nbformat

from backend.modules.connectors.providers import github, github_api
from backend.modules.notebook.models import PublicationModel
from backend.modules.notebook.publish.targets.base import (
    PublishContext,
    PublishError,
    PublishResult,
)
from backend.modules.settings.routes import get_value

SETTING = "notebook.publish.pagesRepo"
DEFAULT_REPO = "notebooks"


def slugify(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "notebook"


def _check(data: Any) -> Any:
    if isinstance(data, dict) and "error" in data:
        raise PublishError(str(data["error"]))
    return data


def _not_found(data: Any) -> bool:
    return isinstance(data, dict) and data.get("status") == 404


async def _put_file(
    owner: str, repo: str, path: str, content: bytes, branch: str, message: str
) -> None:
    endpoint = f"/repos/{owner}/{repo}/contents/{quote(path)}"
    existing = await github_api.request("GET", endpoint, params={"ref": branch})
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    if not _not_found(existing):
        _check(existing)
        if isinstance(existing, dict) and existing.get("sha"):
            body["sha"] = existing["sha"]
    # A repository created a moment ago can answer 409 while its first commit lands.
    for attempt in range(3):
        data = await github_api.request("PUT", endpoint, json=body)
        if isinstance(data, dict) and data.get("status") == 409 and attempt < 2:
            await asyncio.sleep(1.0)
            continue
        _check(data)
        return


async def _delete_file(owner: str, repo: str, path: str, branch: str) -> None:
    endpoint = f"/repos/{owner}/{repo}/contents/{quote(path)}"
    existing = await github_api.request("GET", endpoint, params={"ref": branch})
    if _not_found(existing):
        return
    _check(existing)
    _check(
        await github_api.request(
            "DELETE",
            endpoint,
            json={
                "message": f"Unpublish {path}",
                "sha": existing.get("sha"),
                "branch": branch,
            },
        )
    )


class PagesTarget:
    id = "pages"
    label = "GitHub Pages"
    visibilities = ("public",)
    can_unpublish = True

    def availability(self) -> tuple[bool, str]:
        scopes = github.granted_scopes()
        if scopes is None:
            return False, "Connect GitHub from the home page first."
        if "repo" not in scopes and "public_repo" not in scopes:
            return (
                False,
                "Reconnect GitHub — the current sign-in cannot write to repositories.",
            )
        return True, ""

    async def _repo(self) -> tuple[str, str, dict[str, Any]]:
        user = _check(await github_api.request("GET", "/user"))
        login = str(user.get("login") or "")
        configured = str(get_value(SETTING, "") or "").strip().strip("/")
        if "/" in configured:
            owner, repo = configured.split("/", 1)
        else:
            owner, repo = login, configured or DEFAULT_REPO

        info = await github_api.request("GET", f"/repos/{owner}/{repo}")
        if _not_found(info):
            if owner.lower() != login.lower():
                raise PublishError(
                    f"The repository {owner}/{repo} does not exist, and this app only "
                    "creates repositories on your own account. Create it on GitHub first."
                )
            info = _check(
                await github_api.request(
                    "POST",
                    "/user/repos",
                    json={
                        "name": repo,
                        "description": "Notebooks published from horrible-dashboard",
                        "private": False,
                        "auto_init": True,
                    },
                )
            )
        else:
            _check(info)
        if info.get("private"):
            raise PublishError(
                f"{owner}/{repo} is private. GitHub Pages on a private repository needs "
                "a paid plan, so the link would 404 for everyone. Make it public, or "
                f"name another repository in the {SETTING} setting."
            )
        return owner, repo, info

    async def _ensure_pages(self, owner: str, repo: str, branch: str) -> None:
        site = await github_api.request("GET", f"/repos/{owner}/{repo}/pages")
        if not _not_found(site):
            _check(site)
            return
        body = {"source": {"branch": branch, "path": "/"}}
        result = await github_api.request(
            "POST", f"/repos/{owner}/{repo}/pages", json=body
        )
        if isinstance(result, dict) and "error" in result:
            result = await github_api.request(
                "PUT", f"/repos/{owner}/{repo}/pages", json=body
            )
        if isinstance(result, dict) and "error" in result:
            raise PublishError(
                f"The files are pushed, but GitHub Pages could not be turned on "
                f"({result['error']}). Enable it under Settings → Pages in {owner}/{repo}."
            )

    async def publish(self, ctx: PublishContext) -> PublishResult:
        ok, reason = self.availability()
        if not ok:
            raise PublishError(reason)

        owner, repo, info = await self._repo()
        branch = str(info.get("default_branch") or "main")
        previous = ctx.previous
        slug = str(
            (previous.detail.get("slug") if previous else "") or slugify(ctx.stem)
        )

        html = await asyncio.to_thread(ctx.render_html)
        notebook_file = f"{slug}/{ctx.stem}.ipynb"
        files = [f"{slug}/index.html", notebook_file]

        await _put_file(
            owner, repo, ".nojekyll", b"", branch, "Serve notebooks as plain HTML"
        )
        await _put_file(
            owner, repo, files[0], html.encode("utf-8"), branch, f"Publish {ctx.title}"
        )
        await _put_file(
            owner,
            repo,
            notebook_file,
            nbformat.writes(ctx.notebook).encode("utf-8"),
            branch,
            f"Publish {ctx.title} (notebook)",
        )
        stale = (previous.detail.get("files") if previous else None) or []
        for path in stale:
            if path not in files:
                await _delete_file(owner, repo, str(path), branch)
        await self._ensure_pages(owner, repo, branch)

        if repo.lower() == f"{owner.lower()}.github.io":
            site = f"https://{owner}.github.io/"
        else:
            site = f"https://{owner}.github.io/{repo}/"
        return PublishResult(
            remote_id=f"{owner}/{repo}/{slug}",
            url=f"{site}{slug}/",
            extra_url=(
                f"https://nbviewer.org/github/{owner}/{repo}/blob/{branch}/"
                f"{quote(notebook_file)}"
            ),
            note="GitHub Pages takes about a minute to show a change.",
            detail={
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "slug": slug,
                "files": files,
            },
        )

    async def unpublish(self, record: PublicationModel) -> None:
        detail = record.detail
        owner, repo = str(detail.get("owner") or ""), str(detail.get("repo") or "")
        branch = str(detail.get("branch") or "main")
        if not owner or not repo:
            raise PublishError("This record does not say which repository it lives in.")
        for path in detail.get("files") or []:
            await _delete_file(owner, repo, str(path), branch)
