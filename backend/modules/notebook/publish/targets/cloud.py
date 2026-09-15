"""Publish to Kaggle and Google Colab by reusing the training module's pushers.

Both pushers take a training `ProjectModel` and read four fields from it — `id`,
`name`, `root`, `refs` — so a plain notebook is adapted into a real one rather than a
parallel implementation being grown: the prepared copy is staged as `main.ipynb` in
`$HORRIBLE_DATA_DIR/notebook-publish/<notebook>/`, which is also where the pushers'
bookkeeping lands (`kernel-metadata.json`, `.push-colab.json`). None of that ever
appears in the user's notebook folder.

Credentials stay exactly where training keeps them (`training.kaggle.*` or
`~/.kaggle/kaggle.json`; the training module's Google sign-in), and availability names
that place when they are missing.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import nbformat

from backend import paths
from backend.modules.notebook.models import PublicationModel
from backend.modules.notebook.publish.targets.base import (
    PublishContext,
    PublishError,
    PublishResult,
)
from backend.modules.training.models import ProjectModel


def stage_root(rel_path: str) -> Path:
    """Where a notebook's staged copy and push bookkeeping live, keyed by its path."""
    name = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", rel_path.removesuffix(".ipynb")) or "notebook"
    )
    return paths.data_dir() / "notebook-publish" / name


def kernel_slug(title: str) -> str:
    """Kaggle's kernel id rules: lowercase, digits and hyphens, 5 to 50 characters."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50].strip("-")
    if len(slug) < 5:
        slug = f"{slug}-notebook".strip("-")
    return slug


def _stage(ctx: PublishContext, *, project_id: str, name: str) -> ProjectModel:
    root = stage_root(ctx.rel_path)
    root.mkdir(parents=True, exist_ok=True)
    nbformat.write(ctx.notebook, str(root / "main.ipynb"))
    return ProjectModel(id=project_id, name=name, root=str(root))


def _silent(_line: str) -> None:
    """Push progress lines have no pane to go to here."""


class KaggleTarget:
    id = "kaggle"
    label = "Kaggle"
    visibilities = ("secret", "public")
    #: Kaggle's API has no call for deleting a kernel.
    can_unpublish = False

    def availability(self) -> tuple[bool, str]:
        from backend.modules.training.push import kaggle_push

        if not kaggle_push.has_credentials():
            return (
                False,
                "Add your Kaggle username and API key in Settings "
                "(training.kaggle.username / training.kaggle.key), or put "
                "kaggle.json in ~/.kaggle.",
            )
        return True, ""

    async def publish(self, ctx: PublishContext) -> PublishResult:
        ok, reason = self.availability()
        if not ok:
            raise PublishError(reason)
        from backend.modules.training.push.kaggle_push import KagglePush

        previous = ctx.previous
        # Reuse the title and slug the kernel was created under. Kaggle checks that a
        # title resolves to the kernel id, so renaming the notebook's heading must not
        # change either one after the first push.
        title = str((previous.detail.get("title") if previous else "") or ctx.title)
        slug = str(
            (previous.detail.get("slug") if previous else "") or kernel_slug(title)
        )
        project = await asyncio.to_thread(_stage, ctx, project_id=slug, name=title)
        try:
            result = await asyncio.to_thread(
                KagglePush().push,
                project,
                Path(project.root) / "main.ipynb",
                _silent,
                private=ctx.visibility != "public",
                # A publish is for reading, not training: do not spend GPU quota on it.
                enable_gpu=False,
            )
        except Exception as exc:
            raise PublishError(str(exc)) from exc
        return PublishResult(
            remote_id=slug,
            url=result.url or "",
            note=(
                "Kaggle runs a pushed notebook once on its own machines; its page shows "
                "the output when that run finishes."
            ),
            detail={"slug": slug, "title": title},
        )

    async def unpublish(self, record: PublicationModel) -> None:
        raise PublishError(
            "Kaggle has no API for deleting a notebook. Delete it on kaggle.com, then "
            "choose Forget."
        )


class ColabTarget:
    id = "colab"
    label = "Google Colab"
    visibilities = ("secret", "public")
    can_unpublish = True

    def availability(self) -> tuple[bool, str]:
        from backend.modules.training import google_auth

        if not google_auth.status().get("connected"):
            return (
                False,
                "Sign in to Google from the Training pane (Push to Colab) first — "
                "publishing to Colab uses that sign-in.",
            )
        return True, ""

    async def publish(self, ctx: PublishContext) -> PublishResult:
        ok, reason = self.availability()
        if not ok:
            raise PublishError(reason)
        from backend.modules.training.push.colab_push import ColabPush

        project = await asyncio.to_thread(
            _stage, ctx, project_id=stage_root(ctx.rel_path).name, name=ctx.title
        )
        public = ctx.visibility == "public"
        try:
            result = await asyncio.to_thread(
                ColabPush().push,
                project,
                Path(project.root) / "main.ipynb",
                _silent,
                anyone_with_link=public,
            )
        except Exception as exc:
            raise PublishError(str(exc)) from exc
        url = result.url or ""
        return PublishResult(
            remote_id=url.rsplit("/", 1)[-1],
            url=url,
            note=(
                "Anyone with the link can open it in Colab."
                if public
                else "Only your Google account can open this link."
            ),
        )

    async def unpublish(self, record: PublicationModel) -> None:
        from backend.modules.training.push.colab_push import ColabPush

        root = stage_root(record.path)
        project = ProjectModel(id=root.name, name=root.name, root=str(root))
        try:
            await asyncio.to_thread(ColabPush().delete, project)
        except Exception as exc:
            raise PublishError(str(exc)) from exc
