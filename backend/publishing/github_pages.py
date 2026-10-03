"""GitHub Pages: a public repository of yours, a file set, one commit.

Shared by notebook publishing (one page per notebook) and Scrive (a whole site). Both go
through the `github` connector's token via `github_api.request`, whose errors are
values (`{"error", "status"}`) — `_check` turns them into `PublishError`.

Rules that are quiet if wrong:

- **Always public.** Pages on a private repository needs a paid plan, so a private repo
  yields a link that 404s for everyone. That is refused up front rather than published.
- **`.nojekyll` goes in with every push.** Without it Pages runs Jekyll over the
  repository, which drops any path starting with `_` and adds a minute to every publish.
- **One commit per publish** (`push_files`, Git Data API: blobs → tree → commit → ref).
  The Contents API is one commit *per file*: a site of forty files was forty commits,
  forty Pages builds queued, and a half-published site if anything failed in between.
- **Only changed files are uploaded.** Each file's git blob sha is computed locally
  and compared with the tip's tree, so a site's images and its 3 MB scene runtime are
  sent once, not on every publish. An unchanged set makes no commit at all, so
  publishing twice is free and leaves no noise in the repository's history.
- Pages builds asynchronously; callers say "takes about a minute", because a fresh link
  that 404s looks exactly like a broken one.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from backend.modules.connectors.providers import github, github_api
from backend.publishing.errors import PublishError

NOJEKYLL = ".nojekyll"


@dataclass(frozen=True)
class PagesRepo:
    owner: str
    repo: str
    branch: str

    @property
    def site_url(self) -> str:
        if self.repo.lower() == f"{self.owner.lower()}.github.io":
            return f"https://{self.owner}.github.io/"
        return f"https://{self.owner}.github.io/{self.repo}/"

    def blob_url(self, path: str) -> str:
        return (
            f"https://github.com/{self.owner}/{self.repo}/blob/{self.branch}/"
            f"{quote(path)}"
        )


def check(data: Any) -> Any:
    if isinstance(data, dict) and "error" in data:
        raise PublishError(str(data["error"]))
    return data


def not_found(data: Any) -> bool:
    return isinstance(data, dict) and data.get("status") == 404


def availability() -> tuple[bool, str]:
    """Whether a Pages publish can work right now. No network — panels ask on open."""
    scopes = github.granted_scopes()
    if scopes is None:
        return False, "Connect GitHub from the home page first."
    if "repo" not in scopes and "public_repo" not in scopes:
        return (
            False,
            "Reconnect GitHub — the current sign-in cannot write to repositories.",
        )
    return True, ""


async def resolve_repo(
    configured: str, *, default_repo: str, description: str, setting: str
) -> PagesRepo:
    """`owner/repo`, bare `repo` (on your account) or blank (→ `default_repo`).

    Creates the repository public — with an initial commit, so the Git Data API has a
    ref to build on — when it is missing and on the signed-in account. `setting` is
    named in the private-repo refusal so the person knows where to point elsewhere.
    """
    user = check(await github_api.request("GET", "/user"))
    login = str(user.get("login") or "")
    configured = configured.strip().strip("/")
    if "/" in configured:
        owner, repo = configured.split("/", 1)
    else:
        owner, repo = login, configured or default_repo

    info = await github_api.request("GET", f"/repos/{owner}/{repo}")
    if not_found(info):
        if owner.lower() != login.lower():
            raise PublishError(
                f"The repository {owner}/{repo} does not exist, and this app only "
                "creates repositories on your own account. Create it on GitHub first."
            )
        info = check(
            await github_api.request(
                "POST",
                "/user/repos",
                json={
                    "name": repo,
                    "description": description,
                    "private": False,
                    "auto_init": True,
                },
            )
        )
    else:
        check(info)
    if info.get("private"):
        raise PublishError(
            f"{owner}/{repo} is private. GitHub Pages on a private repository needs "
            "a paid plan, so the link would 404 for everyone. Make it public, or "
            f"name another repository in the {setting} setting."
        )
    return PagesRepo(owner, repo, str(info.get("default_branch") or "main"))


async def ensure_pages(target: PagesRepo, build_type: str = "legacy") -> None:
    """Turn Pages on if it is not already, building the way `build_type` says:

    - `legacy` — serve the branch root as it is (a pushed static site);
    - `workflow` — serve what a GitHub Actions workflow deploys (Scrive's
      jupyter-book mode, whose workflow builds the site in CI).

    A site already on, but building the other way, is switched: a repository that
    moves from one mode to the other must not keep serving the old one.
    """
    base = f"/repos/{target.owner}/{target.repo}/pages"
    body: dict[str, Any] = {"build_type": build_type}
    if build_type == "legacy":
        body["source"] = {"branch": target.branch, "path": "/"}
    site = await github_api.request("GET", base)
    if not not_found(site):
        check(site)
        current = site.get("build_type") if isinstance(site, dict) else None
        if current and current != build_type:
            switched = await github_api.request("PUT", base, json=body)
            if isinstance(switched, dict) and "error" in switched:
                raise PublishError(
                    f"The files are pushed, but GitHub Pages is still set to build "
                    f"from {current!r} ({switched['error']}). Change it under "
                    f"Settings → Pages in {target.owner}/{target.repo}."
                )
        return
    result = await github_api.request("POST", base, json=body)
    if isinstance(result, dict) and "error" in result:
        result = await github_api.request("PUT", base, json=body)
    if isinstance(result, dict) and "error" in result:
        raise PublishError(
            f"The files are pushed, but GitHub Pages could not be turned on "
            f"({result['error']}). Enable it under Settings → Pages in "
            f"{target.owner}/{target.repo}."
        )


async def _head(target: PagesRepo) -> tuple[str, str]:
    """(commit sha, tree sha) at the tip of the branch.

    A repository created a moment ago can answer 404/409 while its initial commit lands,
    so this retries briefly before giving up.
    """
    git = f"/repos/{target.owner}/{target.repo}/git"
    for attempt in range(4):
        ref = await github_api.request("GET", f"{git}/ref/heads/{target.branch}")
        status = ref.get("status") if isinstance(ref, dict) else None
        if status in (404, 409) and attempt < 3:
            await asyncio.sleep(1.0)
            continue
        check(ref)
        commit_sha = str(ref["object"]["sha"])
        commit = check(await github_api.request("GET", f"{git}/commits/{commit_sha}"))
        return commit_sha, str(commit["tree"]["sha"])
    raise PublishError(f"{target.owner}/{target.repo} has no {target.branch} branch.")


def git_blob_sha(content: bytes) -> str:
    """The sha git gives `content` as a blob — what a tree entry names."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


async def _existing_blobs(target: PagesRepo, tree_sha: str) -> dict[str, str]:
    """Every file at the tip, by path, with its blob sha (`""` when not given)."""
    git = f"/repos/{target.owner}/{target.repo}/git"
    tree = check(
        await github_api.request(
            "GET", f"{git}/trees/{tree_sha}", params={"recursive": "1"}
        )
    )
    return {
        str(entry["path"]): str(entry.get("sha") or "")
        for entry in tree.get("tree") or []
        if entry.get("type") == "blob"
    }


async def push_files(
    target: PagesRepo,
    files: Mapping[str, bytes],
    *,
    message: str,
    delete: Iterable[str] = (),
) -> str | None:
    """Write `files` (and remove `delete`) as **one commit**. Returns the new commit's
    sha, or `None` when nothing changed.

    `.nojekyll` is always included. The tip's tree is read first (one call): a file
    whose bytes are already there is skipped, so only changed files are uploaded, and
    deleting a path that is not in the tree is a no-op rather than an error — a stale
    record may name files someone already removed. A ref update that loses a race with
    another push is retried once from the new tip.
    """
    payload = {NOJEKYLL: b"", **files}
    doomed = [p for p in delete if p not in payload]
    git = f"/repos/{target.owner}/{target.repo}/git"

    blobs: dict[str, str] = {}
    for attempt in range(2):
        parent, base_tree = await _head(target)
        present = await _existing_blobs(target, base_tree)
        # A file whose bytes are already at the tip is neither uploaded nor listed:
        # re-publishing a site re-sends only what changed, not every image.
        changed = {
            path: content
            for path, content in payload.items()
            if present.get(path) != git_blob_sha(content)
        }
        for path, content in changed.items():
            if path in blobs:
                continue
            blob = check(
                await github_api.request(
                    "POST",
                    f"{git}/blobs",
                    json={
                        "content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    },
                )
            )
            blobs[path] = str(blob["sha"])
        entries: list[dict[str, Any]] = [
            {"path": path, "mode": "100644", "type": "blob", "sha": blobs[path]}
            for path in changed
        ]
        entries += [
            {"path": path, "mode": "100644", "type": "blob", "sha": None}
            for path in doomed
            if path in present
        ]
        if not entries:
            return None
        tree = check(
            await github_api.request(
                "POST", f"{git}/trees", json={"base_tree": base_tree, "tree": entries}
            )
        )
        if str(tree["sha"]) == base_tree:
            return None
        commit = check(
            await github_api.request(
                "POST",
                f"{git}/commits",
                json={"message": message, "tree": tree["sha"], "parents": [parent]},
            )
        )
        moved = await github_api.request(
            "PATCH",
            f"{git}/refs/heads/{target.branch}",
            json={"sha": commit["sha"], "force": False},
        )
        if isinstance(moved, dict) and moved.get("status") == 422 and attempt == 0:
            continue  # someone pushed in between; rebuild on the new tip
        check(moved)
        return str(commit["sha"])
    return None
