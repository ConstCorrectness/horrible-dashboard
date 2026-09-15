"""Push a project notebook to Kaggle kernels (their free cloud compute).

Generates `kernel-metadata.json` in the project root (competition/dataset
sources from the project's refs) and calls `kernels_push`. Submissions to a
competition stay manual (`kaggle competitions submit …` in the terminal pane) —
this only ships the notebook to run on Kaggle's side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.modules.training.models import ProjectModel, PushResultModel
from backend.modules.training.providers.kaggle_provider import _api
from backend.modules.training.push.base import ProgressLine, PushError


def _username() -> str:
    from backend.modules.settings.routes import get_value

    name = str(get_value("training.kaggle.username", "") or "")
    if name:
        return name
    # Fall back to kaggle.json (the same file the provider auth uses).
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    if cfg.is_file():
        try:
            return str(json.loads(cfg.read_text(encoding="utf-8")).get("username", ""))
        except ValueError:
            pass
    raise PushError("no Kaggle username — set training.kaggle.username in Settings")


def has_credentials() -> bool:
    """Whether a push could authenticate, without importing the kaggle package.

    Cheap on purpose: the notebook publish panel asks on every open, and `_api()`
    imports kaggle and makes a network round trip to find the same thing out.
    """
    from backend.modules.settings.routes import get_value

    if get_value("training.kaggle.username", "") and get_value(
        "training.kaggle.key", ""
    ):
        return True
    return (Path.home() / ".kaggle" / "kaggle.json").is_file()


def kernel_metadata(
    project: ProjectModel,
    username: str,
    *,
    private: bool = True,
    enable_gpu: bool = True,
) -> dict[str, Any]:
    return {
        "id": f"{username}/{project.id}",
        "title": project.name,
        "code_file": "main.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": private,
        "enable_gpu": enable_gpu,
        "enable_internet": True,
        "competition_sources": [
            r.id
            for r in project.refs
            if r.provider == "kaggle" and r.kind == "competition"
        ],
        "dataset_sources": [
            r.id for r in project.refs if r.provider == "kaggle" and r.kind == "dataset"
        ],
    }


class KagglePush:
    target = "kaggle"
    label = "Kaggle kernels"

    def push(
        self,
        project: ProjectModel,
        notebook: Path,
        progress: ProgressLine,
        *,
        private: bool = True,
        enable_gpu: bool = True,
    ) -> PushResultModel:
        """Keyword options default to the training behaviour; notebook publishing
        overrides both (a publish can be public, and should not spend GPU quota)."""
        username = _username()
        meta = kernel_metadata(
            project, username, private=private, enable_gpu=enable_gpu
        )
        meta_path = Path(project.root) / "kernel-metadata.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        progress(f"pushing {meta['id']} to Kaggle kernels…")
        api = _api()
        try:
            api.kernels_push(project.root)
        except Exception as exc:
            raise PushError(f"kaggle kernels push failed: {exc}") from exc
        url = f"https://www.kaggle.com/code/{meta['id']}"
        progress(f"pushed → {url}")
        return PushResultModel(target=self.target, url=url, status="pushed")

    def status(self, project: ProjectModel) -> PushResultModel:
        username = _username()
        kernel_id = f"{username}/{project.id}"
        api = _api()
        try:
            result = api.kernels_status(kernel_id)
        except Exception as exc:
            raise PushError(f"kaggle status failed: {exc}") from exc
        status = str(getattr(result, "status", result))
        return PushResultModel(
            target=self.target,
            url=f"https://www.kaggle.com/code/{kernel_id}",
            status=status,
            detail=str(getattr(result, "failureMessage", "") or ""),
        )
