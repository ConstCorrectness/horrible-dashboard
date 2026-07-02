"""HuggingFace datasets as an environment provider.

Fetch is lazy by design: the scaffolded notebook calls `datasets.load_dataset(id)`
inside the project venv (which caches under the venv user's HF cache), so `fetch`
is a no-op unless `meta.download` asks for a snapshot into `data/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.modules.settings.routes import get_value
from backend.modules.training.models import EnvironmentRefModel
from backend.modules.training.providers.base import (
    FetchResult,
    ProgressFn,
    ProviderError,
    ScaffoldResult,
    code_cell,
    md_cell,
)


def _api() -> Any:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover — dep is in pyproject
        raise ProviderError(f"huggingface_hub not installed: {exc}") from exc
    token = str(get_value("training.hf.token", "") or "") or None
    return HfApi(token=token)


def _ref(info: Any) -> EnvironmentRefModel:
    did = str(getattr(info, "id", "") or "")
    return EnvironmentRefModel(
        provider="huggingface",
        kind="dataset",
        id=did,
        title=did,
        url=f"https://huggingface.co/datasets/{did}",
        meta={
            "downloads": int(getattr(info, "downloads", 0) or 0),
            "likes": int(getattr(info, "likes", 0) or 0),
        },
    )


class HuggingFaceProvider:
    provider = "huggingface"
    label = "Hugging Face"
    kinds = ("dataset",)

    def search(
        self, query: str, kind: str | None, limit: int
    ) -> list[EnvironmentRefModel]:
        api = _api()
        try:
            return [_ref(d) for d in api.list_datasets(search=query, limit=limit)]
        except Exception as exc:
            raise ProviderError(f"HF search failed: {exc}") from exc

    def resolve(self, ref_id: str, kind: str | None) -> EnvironmentRefModel:
        api = _api()
        try:
            return _ref(api.dataset_info(ref_id))
        except Exception as exc:
            raise ProviderError(f"HF dataset not found: {ref_id} ({exc})") from exc

    def fetch(
        self, ref: EnvironmentRefModel, dest: Path, progress: ProgressFn
    ) -> FetchResult:
        if not ref.meta.get("download"):
            progress("HF dataset loads lazily via datasets.load_dataset()", 1.0)
            return FetchResult(note="lazy — loaded by the notebook at runtime")
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(f"huggingface_hub not installed: {exc}") from exc
        progress(f"snapshotting {ref.id} into data/…", None)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            snapshot_download(repo_id=ref.id, repo_type="dataset", local_dir=str(dest))
        except Exception as exc:
            raise ProviderError(f"HF snapshot failed: {exc}") from exc
        files = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
        size = sum((dest / f).stat().st_size for f in files)
        progress(f"fetched {len(files)} files", 1.0)
        return FetchResult(files=files, bytes=size)

    def scaffold(self, ref: EnvironmentRefModel, project: Any) -> ScaffoldResult:
        return ScaffoldResult(
            cells=[
                md_cell(
                    f"# {ref.title or ref.id}\n\nHugging Face dataset "
                    f"[`{ref.id}`]({ref.url}), loaded lazily below."
                ),
                code_cell(
                    "from datasets import load_dataset\n\n"
                    f'ds = load_dataset("{ref.id}")\n'
                    "ds"
                ),
                code_cell(
                    "import horrible_train as ht\n\n"
                    "# ht.log(step=i, loss=...) streams live metrics to the "
                    "Training metrics pane."
                ),
            ],
            requirements=["datasets", "pandas"],
        )
