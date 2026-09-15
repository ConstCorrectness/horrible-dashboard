"""Push a project notebook to Google Colab (via Drive — Colab has no API).

The `.ipynb` uploads to Drive with the Colab mimetype; the returned file id
yields a `colab.research.google.com/drive/<id>` URL. The id is remembered in
`.push-colab.json` next to project.json so re-pushing updates the same file
in place instead of littering Drive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.modules.training import google_auth
from backend.modules.training.models import ProjectModel, PushResultModel
from backend.modules.training.push.base import ProgressLine, PushError

COLAB_MIME = "application/vnd.google.colaboratory"


def _drive() -> Any:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover — dep is in pyproject
        raise PushError(f"google-api-python-client not installed: {exc}") from exc
    return build("drive", "v3", credentials=google_auth.credentials())


class ColabPush:
    target = "colab"
    label = "Google Colab"

    def push(
        self,
        project: ProjectModel,
        notebook: Path,
        progress: ProgressLine,
        *,
        anyone_with_link: bool | None = None,
    ) -> PushResultModel:
        """`anyone_with_link` sets Drive link sharing after the upload; None (the
        training default) leaves whatever sharing the file already has alone."""
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:  # pragma: no cover
            raise PushError(f"google-api-python-client not installed: {exc}") from exc

        drive = _drive()
        media = MediaFileUpload(str(notebook), mimetype=COLAB_MIME, resumable=False)
        remembered = self._remembered_file_id(project)
        try:
            if remembered:
                progress(f"updating Drive file {remembered}…")
                file = (
                    drive.files()
                    .update(fileId=remembered, media_body=media, fields="id")
                    .execute()
                )
            else:
                progress("uploading notebook to Drive…")
                file = (
                    drive.files()
                    .create(
                        body={"name": f"{project.name}.ipynb", "mimeType": COLAB_MIME},
                        media_body=media,
                        fields="id",
                    )
                    .execute()
                )
        except PushError:
            raise
        except Exception as exc:
            raise PushError(f"Drive upload failed: {exc}") from exc
        file_id = str(file["id"])
        self._remember_file_id(project, file_id)
        if anyone_with_link is not None:
            self._set_link_sharing(drive, file_id, anyone_with_link)
        url = f"https://colab.research.google.com/drive/{file_id}"
        progress(f"pushed → {url}")
        return PushResultModel(target=self.target, url=url, status="pushed")

    def status(self, project: ProjectModel) -> PushResultModel:
        file_id = self._remembered_file_id(project)
        if not file_id:
            return PushResultModel(target=self.target, status="never pushed")
        return PushResultModel(
            target=self.target,
            url=f"https://colab.research.google.com/drive/{file_id}",
            status="pushed",
        )

    def delete(self, project: ProjectModel) -> None:
        """Delete the pushed Drive file and forget it. Safe when nothing was pushed.

        A file already gone from Drive counts as deleted: the person asked for it not
        to exist, and it does not.
        """
        file_id = self._remembered_file_id(project)
        if file_id:
            try:
                _drive().files().delete(fileId=file_id).execute()
            except PushError:
                raise
            except Exception as exc:
                if "404" not in str(exc) and "notFound" not in str(exc):
                    raise PushError(f"Drive delete failed: {exc}") from exc
        self._marker(project).unlink(missing_ok=True)

    @staticmethod
    def _set_link_sharing(drive: Any, file_id: str, enabled: bool) -> None:
        """Turn "anyone with the link can view" on or off for one Drive file."""
        try:
            listed = (
                drive.permissions()
                .list(fileId=file_id, fields="permissions(id,type)")
                .execute()
            )
            anyone = [
                p for p in listed.get("permissions", []) if p.get("type") == "anyone"
            ]
            if enabled and not anyone:
                drive.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"},
                    fields="id",
                ).execute()
            elif not enabled:
                for permission in anyone:
                    drive.permissions().delete(
                        fileId=file_id, permissionId=permission["id"]
                    ).execute()
        except Exception as exc:
            raise PushError(
                f"Uploaded, but could not change who can open the link: {exc}"
            ) from exc

    # The Drive file id persists in a side file next to project.json, so a
    # re-push updates the same Drive file instead of littering new copies.

    @staticmethod
    def _marker(project: ProjectModel) -> Path:
        return Path(project.root) / ".push-colab.json"

    @classmethod
    def _remembered_file_id(cls, project: ProjectModel) -> str | None:
        marker = cls._marker(project)
        if not marker.is_file():
            return None
        try:
            file_id = json.loads(marker.read_text(encoding="utf-8")).get("fileId")
        except ValueError:
            return None
        return str(file_id) if file_id else None

    @classmethod
    def _remember_file_id(cls, project: ProjectModel, file_id: str) -> None:
        cls._marker(project).write_text(
            json.dumps({"fileId": file_id}), encoding="utf-8"
        )
