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
        self, project: ProjectModel, notebook: Path, progress: ProgressLine
    ) -> PushResultModel:
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
