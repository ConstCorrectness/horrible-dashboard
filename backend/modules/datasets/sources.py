"""Where training material comes from: the pluggable source layer.

A `DatasetSource` is a thin, synchronous adapter around one place datasets live,
mirroring the training module's `EnvironmentProvider` and the database module's
driver protocol so there is one shape to learn. Sources lazy-import their client
library inside methods, so a missing optional dependency fails with a clear message
instead of breaking boot.

Four ship:

- **`hub`** — Hugging Face, peeked through the datasets-server without downloading.
- **`local`** — `.jsonl`/`.json`/`.csv`/`.parquet` under the data dir *or* any
  training project's `data/` directory (refs `project:<id>/<file>`), read with the
  stdlib where possible so a peek needs no `datasets`. Spanning both is what makes
  a provider fetch inspectable: it lands under `training.projectsRoot`, nowhere
  near the data dir.
- **`exports`** — what `evals.export` and `trajectories.export` already write. This
  is the spoke that closes the flywheel: "fine-tune on what the model got wrong"
  was fully implemented at both ends and joined by nothing but a path you had to
  type from memory.
- **`kaggle`** — delegates to the training module's existing Kaggle provider rather
  than opening a second Kaggle client.

New sources plug in two ways: a file in this package (built-in) or a backend plugin
calling `host.add_dataset_source(source)` (see `backend.sdk`).
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, NamedTuple, Protocol, runtime_checkable

from backend import paths
from backend.modules.datasets.models import DatasetRefModel

logger = logging.getLogger(__name__)

#: Rows a local peek will read before giving up. A peek is a *look*, and reading a
#: two-gigabyte jsonl to show five rows is the failure this number prevents.
PEEK_SCAN_LIMIT = 500


class SourceError(RuntimeError):
    """The source could not answer. Carries something worth showing the user."""


@runtime_checkable
class DatasetSource(Protocol):
    """Uniform contract every dataset source implements."""

    id: str
    label: str
    #: True when this source's refs are paths on this machine rather than remote
    #: ids. Decides whether a recipe emits `load_dataset('json', data_files=...)`
    #: or `load_dataset('<id>')` — a distinction that does not fail cleanly.
    local: bool

    def search(self, query: str, limit: int) -> list[DatasetRefModel]:
        """Find datasets matching `query`. Raise SourceError on failure."""
        ...

    def splits(self, ref: str) -> list[dict[str, str]]:
        """Every (config, split) this dataset offers."""
        ...

    def peek(
        self, ref: str, config: str, split: str, limit: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """(columns, rows) — real values, cheaply."""
        ...

    def locate(self, ref: str) -> str:
        """The concrete path a local source's ref resolves to; '' when remote."""
        ...


def data_root() -> Path:
    """Where datasets this node owns live. `paths` is the one authority."""
    root = paths.data_dir() / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --- helpers shared by the file-backed sources -------------------------------


def _read_rows(path: Path, limit: int) -> tuple[list[str], list[dict[str, Any]]]:
    """Columns and up to `limit` rows from a local file, without `datasets`.

    Deliberately stdlib for jsonl/json/csv: peeking is what the user does *before*
    deciding to train, and making it depend on a project venv that may not have
    `datasets` installed yet would put the check behind the thing it is meant to
    check. Parquet is the exception and says so.
    """
    suffix = path.suffix.lower()
    rows: list[dict[str, Any]] = []
    if suffix in (".jsonl", ".ndjson"):
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= PEEK_SCAN_LIMIT or len(rows) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    elif suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceError(f"could not read {path.name}: {exc}") from exc
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("rows") or [payload]
        rows = [r for r in (payload or [])[:limit] if isinstance(r, dict)]
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                if index >= limit:
                    break
                rows.append(dict(row))
    elif suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # noqa: PLC0415 — optional, see docstring
        except ImportError as exc:
            raise SourceError(
                "reading a parquet file needs pyarrow, which is not installed. "
                "The dataset is still trainable — only the preview needs it."
            ) from exc
        table = pq.ParquetFile(str(path)).read_row_group(0).slice(0, limit)
        rows = [dict(r) for r in table.to_pylist()]
    else:
        raise SourceError(f"unsupported file type {suffix or '(none)'}")

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns, rows


def _describe_file(
    path: Path, root: Path, source: str, prefix: str = "", origin: str = ""
) -> DatasetRefModel:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    rel = str(path.relative_to(root)).replace("\\", "/")
    size_text = f"{size / 1024:.0f} KB"
    return DatasetRefModel(
        source=source,
        id=f"{prefix}/{rel}" if prefix else rel,
        title=path.name,
        # The same `train.jsonl` exists in every project, so a bare filename is not
        # enough to tell three of them apart in a picker.
        description=f"{size_text} · {origin}" if origin else size_text,
        meta={"bytes": size, "suffix": path.suffix.lower(), "origin": origin},
    )


class _Root(NamedTuple):
    """One directory a file source spans.

    `prefix` is the leading ref segment that selects this root (empty for the
    source's own directory, whose refs stay bare so refs saved before there was
    more than one root keep resolving). `origin` is what a picker shows to tell
    two identically-named files apart.
    """

    prefix: str
    path: Path
    origin: str = ""


def _project_roots() -> list[_Root]:
    """`data/` under each training project, as `project:<id>` prefixed roots.

    A path segment cannot contain `:` on Windows and the prefixes are matched
    first, so these can never collide with a real file under the data dir. The
    training module is imported lazily and failures are swallowed: a broken or
    absent projects root must degrade `local` to the data dir, not empty it.
    """
    try:
        from backend.modules.training import projects  # noqa: PLC0415

        found = projects.list_projects()
    except Exception:  # pragma: no cover - defensive; a bad project.json is not fatal
        logger.debug(
            "could not list training projects for the local source", exc_info=True
        )
        return []
    return [
        _Root(
            f"project:{project.id}",
            Path(project.root) / "data",
            f"project {project.name}",
        )
        for project in found
    ]


class _FileSource:
    """Shared implementation for the file-backed sources.

    `local` and `exports` differ only in which directories they look at and whether
    they are writable, so they share everything else rather than being two copies
    that drift.
    """

    id = ""
    label = ""
    local = True
    suffixes = (".jsonl", ".ndjson", ".json", ".csv", ".parquet")

    def root(self) -> Path:
        raise NotImplementedError

    def roots(self) -> list[_Root]:
        """Every directory this source spans, the unprefixed one first."""
        return [_Root("", self.root())]

    def _resolve(self, ref: str) -> Path:
        ref = ref.replace("\\", "/").lstrip("/")
        roots = self.roots()
        # Prefixes are matched before the bare root, so a prefixed ref can never be
        # shadowed by a same-named directory inside the source's own root.
        chosen = next(
            (r for r in roots if r.prefix and ref.startswith(f"{r.prefix}/")),
            None,
        )
        if chosen is not None:
            ref = ref[len(chosen.prefix) + 1 :]
        else:
            chosen = next((r for r in roots if not r.prefix), None)
            if chosen is None:
                raise SourceError(f"no {self.id} root holds {ref!r}")
        root = chosen.path.resolve()
        target = (root / ref).resolve()
        if not target.is_relative_to(root):
            # A `..` is how a peek route becomes an arbitrary-file-read route.
            raise SourceError(f"{ref!r} escapes the {self.id} directory")
        if not target.is_file():
            raise SourceError(f"no such file: {ref}")
        return target

    def search(self, query: str, limit: int) -> list[DatasetRefModel]:
        needle = query.lower().strip()
        found: list[DatasetRefModel] = []
        for entry in self.roots():
            if len(found) >= limit:
                break
            root = entry.path
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if len(found) >= limit:
                    break
                if not path.is_file() or path.suffix.lower() not in self.suffixes:
                    continue
                if needle and needle not in path.name.lower():
                    continue
                found.append(
                    _describe_file(path, root, self.id, entry.prefix, entry.origin)
                )
        return found

    def splits(self, ref: str) -> list[dict[str, str]]:
        # A file is one split. Saying so beats an empty list, which the pane would
        # render as "this dataset has no splits" — a different and wrong fact.
        return [{"config": "", "split": "train"}]

    def peek(
        self, ref: str, config: str, split: str, limit: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        return _read_rows(self._resolve(ref), limit)

    def locate(self, ref: str) -> str:
        return str(self._resolve(ref))


class LocalSource(_FileSource):
    """The data dir *and* every training project's `data/` directory.

    A provider fetch (Kaggle, Hub, a competition) writes into
    `<project.root>/data`, which is under `training.projectsRoot` and so outside
    the data dir entirely. Spanning both is what makes "fetch it, then look at it"
    a real instruction rather than a path you copy out of the backend log.
    """

    id = "local"
    label = "Local files"

    def root(self) -> Path:
        return data_root()

    def roots(self) -> list[_Root]:
        return [_Root("", data_root()), *_project_roots()]


class ExportsSource(_FileSource):
    id = "exports"
    label = "Exports (evals & trajectories)"

    def root(self) -> Path:
        # Both exporters write under the data dir; this source spans them so
        # "train on what the model got wrong" is one click from where it was
        # produced rather than a path copied by hand.
        return paths.data_dir()

    def search(self, query: str, limit: int) -> list[DatasetRefModel]:
        root = self.root()
        needle = query.lower().strip()
        found: list[DatasetRefModel] = []
        for sub, origin in (
            ("evals/exports", "evals"),
            ("trajectories/exports", "trajectories"),
        ):
            folder = root / sub
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.jsonl")):
                if len(found) >= limit:
                    break
                if needle and needle not in path.name.lower():
                    continue
                ref = _describe_file(path, root, self.id)
                ref.meta["origin"] = origin
                ref.description = f"{origin} export — {ref.description}"
                found.append(ref)
        return found


class HubSource:
    """Hugging Face, through the datasets-server. Never downloads to peek."""

    id = "hub"
    label = "Hugging Face Hub"
    local = False

    def search(self, query: str, limit: int) -> list[DatasetRefModel]:
        try:
            from huggingface_hub import HfApi  # noqa: PLC0415 — see module docstring
        except ImportError as exc:  # pragma: no cover - a core dep today
            raise SourceError("huggingface-hub is not installed") from exc
        try:
            found = HfApi().list_datasets(search=query or None, limit=limit)
        except Exception as exc:  # noqa: BLE001 — network/auth, shown to the user
            raise SourceError(f"could not reach the Hub: {exc}") from exc
        out: list[DatasetRefModel] = []
        for item in found:
            ident = getattr(item, "id", "")
            if not ident:
                continue
            out.append(
                DatasetRefModel(
                    source=self.id,
                    id=ident,
                    title=ident,
                    url=f"https://huggingface.co/datasets/{ident}",
                    rows=None,
                    description=", ".join(getattr(item, "tags", [])[:4]),
                    meta={"downloads": getattr(item, "downloads", 0)},
                )
            )
        return out

    def splits(self, ref: str) -> list[dict[str, str]]:
        return _run_async(_hub_splits(ref))

    def peek(
        self, ref: str, config: str, split: str, limit: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        payload = _run_async(_hub_first_rows(ref, config, split, limit))
        return payload["columns"], payload["rows"]

    def locate(self, ref: str) -> str:
        return ""


class KaggleSource:
    """Kaggle datasets, through the training module's existing provider.

    Delegation rather than a second client: the Kaggle provider already owns
    credential handling and the `kaggle` import, and two clients would mean two
    places to fix when the API changes.
    """

    id = "kaggle"
    label = "Kaggle"
    local = False

    def _provider(self) -> Any:
        from backend.modules.training.providers import kaggle_provider  # noqa: PLC0415

        return kaggle_provider.KaggleProvider()

    def search(self, query: str, limit: int) -> list[DatasetRefModel]:
        from backend.modules.training.providers.base import ProviderError  # noqa: PLC0415

        try:
            refs = self._provider().search(query, "dataset", limit)
        except ProviderError as exc:
            raise SourceError(str(exc)) from exc
        return [
            DatasetRefModel(
                source=self.id,
                id=ref.id,
                title=ref.title,
                url=ref.url,
                description="",
                meta=dict(ref.meta or {}),
            )
            for ref in refs
        ]

    def splits(self, ref: str) -> list[dict[str, str]]:
        return [{"config": "", "split": "train"}]

    def peek(
        self, ref: str, config: str, split: str, limit: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        # A Kaggle dataset is a zip of arbitrary files; there is no server-side
        # row endpoint to ask. Saying so is better than downloading gigabytes
        # behind a "preview" button.
        raise SourceError(
            "Kaggle has no row-preview endpoint. Fetch the dataset into a training "
            "project first (POST /api/training/projects/{id}/fetch); the Local "
            "source spans project data dirs, so the file is peekable there as "
            "`project:<project id>/<file>`."
        )

    def locate(self, ref: str) -> str:
        return ""


# --- the async bridge --------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run one coroutine from this synchronous protocol.

    The sources are sync by contract (routes call them via `asyncio.to_thread`,
    matching the training providers), but the Hub peek is httpx-async. Calling
    `asyncio.run` from a worker thread is safe precisely because that thread has no
    running loop — doing it on the event-loop thread would raise, which is why
    every route below goes through `to_thread`.
    """
    import asyncio  # noqa: PLC0415 — only this bridge needs it

    return asyncio.run(coro)


async def _hub_splits(ref: str) -> list[dict[str, str]]:
    from backend.modules.datasets import hub  # noqa: PLC0415

    try:
        return await hub.splits(ref)
    except hub.PeekError as exc:
        raise SourceError(str(exc)) from exc


async def _hub_first_rows(
    ref: str, config: str, split: str, limit: int
) -> dict[str, Any]:
    from backend.modules.datasets import hub  # noqa: PLC0415

    try:
        return await hub.first_rows(ref, config, split or "train", limit)
    except hub.PeekError as exc:
        raise SourceError(str(exc)) from exc


# --- registry ----------------------------------------------------------------

_BUILTIN: tuple[DatasetSource, ...] = (
    HubSource(),
    LocalSource(),
    ExportsSource(),
    KaggleSource(),
)


def all_sources() -> dict[str, DatasetSource]:
    """Built-ins plus any a backend plugin registered. Built-ins win id conflicts,
    matching `training.providers` and `search.providers`."""
    from backend.sdk.registry import registry  # noqa: PLC0415 — avoids an import cycle

    out: dict[str, DatasetSource] = dict(getattr(registry, "dataset_sources", {}) or {})
    for source in _BUILTIN:
        out[source.id] = source
    return out


def get_source(source_id: str) -> DatasetSource:
    found = all_sources().get(source_id)
    if found is None:
        known = ", ".join(sorted(all_sources()))
        raise SourceError(f"unknown dataset source {source_id!r}. Known: {known}")
    return found
