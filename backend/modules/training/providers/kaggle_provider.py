"""Kaggle competitions + datasets as an environment provider.

Credentials come from the settings store (`training.kaggle.username/key`), falling
back to `~/.kaggle/kaggle.json`. The kaggle client authenticates at import time, so
the import happens inside `_api()` after the env vars are staged — the driver-style
lazy import also keeps a missing/misconfigured kaggle package from breaking boot.
"""

from __future__ import annotations

import os
import zipfile
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
    username = str(get_value("training.kaggle.username", "") or "")
    key = str(get_value("training.kaggle.key", "") or "")
    if username and key:
        os.environ["KAGGLE_USERNAME"] = username
        os.environ["KAGGLE_KEY"] = key
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError as exc:  # pragma: no cover — dep is in pyproject
        raise ProviderError(f"kaggle package not installed: {exc}") from exc
    try:
        api = KaggleApi()
        api.authenticate()
    except Exception as exc:
        raise ProviderError(
            "Kaggle authentication failed — set training.kaggle.username/key in "
            f"Settings or provide ~/.kaggle/kaggle.json ({exc})"
        ) from exc
    return api


def _as_items(result: Any, attr: str) -> list[Any]:
    """Normalize a Kaggle list call to a plain list of items. Kaggle 2.2+ wraps
    some list calls in a response object (e.g. `competitions_list` →
    `ApiListCompetitionsResponse`, whose `.competitions` holds the items) while
    others still return a bare list — so we unwrap the named attribute when present
    and otherwise treat the result as the list itself. `None` → empty."""
    if result is None:
        return []
    items = getattr(result, attr, None)
    if items is not None:
        return list(items)
    return list(result)


def _competition_ref(c: Any) -> EnvironmentRefModel:
    ref = str(getattr(c, "ref", "") or "")
    cid = ref.rsplit("/", 1)[-1]
    return EnvironmentRefModel(
        provider="kaggle",
        kind="competition",
        id=cid,
        title=str(getattr(c, "title", "") or cid),
        url=f"https://www.kaggle.com/competitions/{cid}",
        meta={
            "deadline": str(getattr(c, "deadline", "") or ""),
            "reward": str(getattr(c, "reward", "") or ""),
            "category": str(getattr(c, "category", "") or ""),
        },
    )


def _dataset_ref(d: Any) -> EnvironmentRefModel:
    ref = str(getattr(d, "ref", "") or "")
    return EnvironmentRefModel(
        provider="kaggle",
        kind="dataset",
        id=ref,  # datasets are addressed as owner/slug
        title=str(getattr(d, "title", "") or ref),
        url=f"https://www.kaggle.com/datasets/{ref}",
        meta={"size": str(getattr(d, "size", "") or "")},
    )


class KaggleProvider:
    provider = "kaggle"
    label = "Kaggle"
    kinds = ("competition", "dataset")

    def search(
        self, query: str, kind: str | None, limit: int
    ) -> list[EnvironmentRefModel]:
        api = _api()
        out: list[EnvironmentRefModel] = []
        try:
            if kind in (None, "competition"):
                for c in _as_items(api.competitions_list(search=query), "competitions"):
                    out.append(_competition_ref(c))
            if kind in (None, "dataset"):
                for d in _as_items(api.dataset_list(search=query), "datasets"):
                    out.append(_dataset_ref(d))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Kaggle search failed: {exc}") from exc
        return out[:limit]

    def resolve(self, ref_id: str, kind: str | None) -> EnvironmentRefModel:
        # Dataset ids contain a slash (owner/slug); competition ids don't.
        inferred = kind or ("dataset" if "/" in ref_id else "competition")
        api = _api()
        try:
            if inferred == "competition":
                slug = ref_id.rsplit("/", 1)[-1]
                for c in _as_items(api.competitions_list(search=slug), "competitions"):
                    ref = _competition_ref(c)
                    if ref.id == slug:
                        return ref
                raise ProviderError(f"competition not found: {ref_id}")
            for d in _as_items(
                api.dataset_list(search=ref_id.rsplit("/", 1)[-1]), "datasets"
            ):
                ref = _dataset_ref(d)
                if ref.id == ref_id:
                    return ref
            raise ProviderError(f"dataset not found: {ref_id}")
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Kaggle resolve failed: {exc}") from exc

    def fetch(
        self, ref: EnvironmentRefModel, dest: Path, progress: ProgressFn
    ) -> FetchResult:
        api = _api()
        dest.mkdir(parents=True, exist_ok=True)
        progress(f"downloading {ref.kind} {ref.id} from Kaggle…", None)
        try:
            if ref.kind == "competition":
                api.competition_download_files(ref.id, path=str(dest), quiet=True)
            else:
                api.dataset_download_files(
                    ref.id, path=str(dest), quiet=True, unzip=True
                )
        except Exception as exc:
            raise ProviderError(f"Kaggle download failed: {exc}") from exc
        # Competition downloads arrive as one zip; unpack and drop it.
        for archive in list(dest.glob("*.zip")):
            progress(f"unpacking {archive.name}…", None)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
            archive.unlink()
        files = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
        size = sum((dest / f).stat().st_size for f in files)
        progress(f"fetched {len(files)} files", 1.0)
        return FetchResult(files=files, bytes=size)

    def scaffold(self, ref: EnvironmentRefModel, project: Any) -> ScaffoldResult:
        title = ref.title or ref.id
        return ScaffoldResult(
            cells=[
                md_cell(
                    f"# {title}\n\nKaggle {ref.kind} [`{ref.id}`]({ref.url}). "
                    "Data lives in `data/`; this venv is project-local."
                ),
                code_cell(
                    "from pathlib import Path\n\n"
                    "import pandas as pd\n\n"
                    'DATA = Path("data")\n'
                    "print(sorted(p.name for p in DATA.rglob('*') if p.is_file()))"
                ),
                code_cell(
                    "# Peek at the first csv — adjust to the competition's layout.\n"
                    "csvs = sorted(DATA.rglob('*.csv'))\n"
                    "df = pd.read_csv(csvs[0]) if csvs else None\n"
                    "df.head() if df is not None else 'no csv files in data/'"
                ),
                code_cell(
                    "import horrible_train as ht\n\n"
                    "# ht.log(step=i, loss=...) streams live metrics to the "
                    "Training metrics pane."
                ),
            ],
            requirements=["pandas", "numpy", "scikit-learn"],
        )
