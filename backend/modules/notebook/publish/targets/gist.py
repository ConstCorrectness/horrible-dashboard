"""Publish to a GitHub Gist, rendered through nbviewer.

The shared link is the **nbviewer** render (`nbviewer.org/gist/<owner>/<id>`), with the
gist page as the second link: GitHub's own notebook renderer gives up on large notebooks
and shows "Sorry, something went wrong", which is a poor first impression to send
somebody. A *secret* gist still renders on nbviewer — secret on GitHub means unlisted,
not private: anyone holding the link can read it, and the panel says so.

GitHub cannot change a gist's visibility after creation, so switching between secret and
public creates a new gist and deletes the old one. The old link stops working; the result
note says that out loud rather than letting it be discovered.
"""

from __future__ import annotations

from typing import Any

import nbformat

from backend.modules.connectors.providers import github, github_api
from backend.modules.notebook.models import PublicationModel
from backend.modules.notebook.publish.targets.base import (
    PublishContext,
    PublishError,
    PublishResult,
)


def _check(data: Any) -> Any:
    if isinstance(data, dict) and "error" in data:
        raise PublishError(str(data["error"]))
    return data


class GistTarget:
    id = "gist"
    label = "GitHub Gist"
    visibilities = ("secret", "public")
    can_unpublish = True

    def availability(self) -> tuple[bool, str]:
        scopes = github.granted_scopes()
        if scopes is None:
            return False, "Connect GitHub from the home page first."
        if "gist" not in scopes:
            return (
                False,
                "Reconnect GitHub to allow gists — your current sign-in was made "
                "before this app asked for gist access.",
            )
        return True, ""

    async def publish(self, ctx: PublishContext) -> PublishResult:
        ok, reason = self.availability()
        if not ok:
            raise PublishError(reason)

        filename = f"{ctx.stem}.ipynb"
        files: dict[str, Any] = {filename: {"content": nbformat.writes(ctx.notebook)}}
        previous = ctx.previous
        note = ""

        data: Any = None
        if previous and previous.remote_id and previous.visibility == ctx.visibility:
            old_name = str(previous.detail.get("filename") or filename)
            if old_name != filename:
                # A renamed file would otherwise sit beside the new one forever.
                files[old_name] = None
            data = await github_api.request(
                "PATCH",
                f"/gists/{previous.remote_id}",
                json={"description": ctx.title, "files": files},
            )
            if isinstance(data, dict) and data.get("status") == 404:
                # Deleted on GitHub since we published. Make a fresh one rather than
                # failing on a record that no longer points anywhere.
                data = None
                files = {filename: files[filename]}
            else:
                _check(data)

        if data is None:
            data = _check(
                await github_api.request(
                    "POST",
                    "/gists",
                    json={
                        "description": ctx.title,
                        "public": ctx.visibility == "public",
                        "files": files,
                    },
                )
            )
            if (
                previous
                and previous.remote_id
                and previous.visibility != ctx.visibility
            ):
                await github_api.request("DELETE", f"/gists/{previous.remote_id}")
                note = (
                    "GitHub cannot change a gist's visibility, so this is a new gist and "
                    "the old link no longer works."
                )

        gist_id = str(data.get("id") or "")
        if not gist_id:
            raise PublishError("GitHub accepted the gist but returned no id.")
        owner = str((data.get("owner") or {}).get("login") or "")
        viewer = (
            f"https://nbviewer.org/gist/{owner}/{gist_id}"
            if owner
            else f"https://nbviewer.org/gist/{gist_id}"
        )
        if ctx.visibility == "secret" and not note:
            note = "Secret means unlisted: anyone with the link can read it."
        return PublishResult(
            remote_id=gist_id,
            url=viewer,
            extra_url=str(data.get("html_url") or ""),
            note=note,
            detail={"filename": filename, "owner": owner},
        )

    async def unpublish(self, record: PublicationModel) -> None:
        data = await github_api.request("DELETE", f"/gists/{record.remote_id}")
        if isinstance(data, dict) and "error" in data and data.get("status") != 404:
            raise PublishError(str(data["error"]))
