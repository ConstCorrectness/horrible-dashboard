"""The GGUF catalog: which weights this node can actually serve, and getting more.

Ollama has been hiding weight files from this codebase entirely — the agent knew
model *names*, never files — so the moment the node serves a model itself, "which
GGUFs do I have, how big are they, and where did they come from" becomes a real
question with no existing answer.

Three origins, and the distinction is load-bearing rather than cosmetic:

- ``managed`` — under ``$HORRIBLE_DATA_DIR/llamacpp/models``. Ours: downloadable,
  deletable, and counted against the disk budget.
- ``ollama`` / ``lmstudio`` — files those apps own. **Serveable, never touched.**
  llama-server memory-maps a GGUF read-only, so pointing it at LM Studio's copy
  costs nothing and duplicating a 20 GB file to "own" it would be absurd; but a
  delete route that could reach into another app's store is a footgun, so origin
  gates deletion.
- ``extra`` — directories the user added via the ``llamacpp.modelDirs`` setting.

Header facts (architecture, parameter count, quantization) come from the module's
own GGUF reader, cached on (path, size, mtime) because scanning a directory of
20 GB files on every status poll would re-read all of them.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from backend.modules.interpretability import gguf
from backend import paths

logger = logging.getLogger(__name__)

HF_ENDPOINT = "https://huggingface.co"

#: Default ceiling for the managed directory, in gigabytes. A quantized 7B is
#: ~4 GB and a 70B is ~40 GB, so the budget has to be generous enough to be useful
#: and present enough that a one-click download can't quietly fill the disk.
DEFAULT_DISK_BUDGET_GB = 80.0


@dataclass(frozen=True)
class ModelFile:
    """One GGUF on disk."""

    path: Path
    origin: str  # managed | ollama | lmstudio | extra
    name: str
    size_bytes: int
    architecture: str = ""
    parameters: int | None = None
    context_length: int | None = None
    quantization: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "origin": self.origin,
            "name": self.name,
            "sizeBytes": self.size_bytes,
            "architecture": self.architecture,
            "parameters": self.parameters,
            "contextLength": self.context_length,
            "quantization": self.quantization,
            "error": self.error,
            "deletable": self.origin == "managed",
        }


def models_root() -> Path:
    return paths.data_dir() / "llamacpp" / "models"


def _setting(key: str, default: Any) -> Any:
    from backend.modules.settings.routes import get_value

    return get_value(key, default)


def extra_dirs() -> list[Path]:
    """User-declared GGUF directories (``llamacpp.modelDirs``, newline or ; separated)."""
    raw = _setting("llamacpp.modelDirs", "") or ""
    if not isinstance(raw, str):
        return []
    parts = [p.strip() for p in re.split(r"[\n;]+", raw) if p.strip()]
    return [Path(p).expanduser() for p in parts]


def disk_budget_bytes() -> int:
    value = _setting("llamacpp.diskBudgetGb", DEFAULT_DISK_BUDGET_GB)
    try:
        gb = float(value)
    except (TypeError, ValueError):
        gb = DEFAULT_DISK_BUDGET_GB
    return int(max(gb, 0.0) * 1024**3)


def managed_bytes() -> int:
    root = models_root()
    if not root.is_dir():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*.gguf") if p.is_file())


# (path, size, mtime) → header facts. A GGUF's header never changes without the
# file changing, so this is a pure memo rather than a TTL cache.
_header_cache: dict[tuple[str, int, int], dict[str, Any]] = {}


def _header_facts(path: Path, size: int, mtime: int) -> dict[str, Any]:
    key = (str(path), size, mtime)
    cached = _header_cache.get(key)
    if cached is not None:
        return cached
    facts: dict[str, Any] = {}
    try:
        header = gguf.read_header(path)
        meta = header.metadata
        arch = str(meta.get("general.architecture") or "")
        facts["architecture"] = arch
        name = meta.get("general.name")
        if isinstance(name, str):
            facts["label"] = name
        ctx = meta.get(f"{arch}.context_length") if arch else None
        if isinstance(ctx, int):
            facts["context_length"] = ctx
        facts["parameters"] = sum(t.elements for t in header.tensors) or None
        # The quantization label is the most common tensor type, not
        # `general.file_type`: that KV is an enum id whose meaning has shifted
        # between llama.cpp releases, and a mixed-quant build (most K-quant files
        # keep some tensors at F32) is the norm — so the histogram is both more
        # accurate and more honest than a single declared code.
        counts: dict[str, int] = {}
        for tensor in header.tensors:
            counts[tensor.type_name] = counts.get(tensor.type_name, 0) + 1
        if counts:
            facts["quantization"] = max(counts.items(), key=lambda kv: kv[1])[0]
    except (OSError, gguf.GgufError, ValueError) as exc:
        logger.info("llamacpp: unreadable GGUF header %s (%s)", path, exc)
        facts["error"] = str(exc)
    _header_cache[key] = facts
    return facts


def _describe(path: Path, origin: str) -> ModelFile | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    facts = _header_facts(path, stat.st_size, int(stat.st_mtime))
    return ModelFile(
        path=path,
        origin=origin,
        name=str(facts.get("label") or path.stem),
        size_bytes=stat.st_size,
        architecture=str(facts.get("architecture") or ""),
        parameters=facts.get("parameters"),
        context_length=facts.get("context_length"),
        quantization=str(facts.get("quantization") or ""),
        error=str(facts.get("error") or ""),
    )


def _scan(root: Path, origin: str) -> list[ModelFile]:
    if not root.is_dir():
        return []
    found: list[ModelFile] = []
    for path in sorted(root.rglob("*.gguf")):
        # A multimodal repo ships its vision projector beside the weights. It is a
        # real GGUF that parses cleanly and cannot be served as a chat model — the
        # same trap the model explorer hits from the other direction.
        if path.name.startswith("mmproj-"):
            continue
        entry = _describe(path, origin)
        if entry is not None:
            found.append(entry)
    return found


def _ollama_blobs() -> list[ModelFile]:
    """GGUFs in Ollama's blob store, named by the model tag that points at them.

    Ollama stores weights as content-addressed blobs with no extension, so the
    directory scan above cannot see them; the manifest tree is the only thing that
    maps a name onto a blob.
    """
    root = gguf.ollama_root()
    manifests = root / "manifests"
    if not manifests.is_dir():
        return []
    found: list[ModelFile] = []
    seen: set[str] = set()
    for manifest in manifests.rglob("*"):
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        layers = data.get("layers")
        if not isinstance(layers, list):
            continue
        digest = ""
        for layer in layers:
            if isinstance(layer, dict) and str(layer.get("mediaType", "")).endswith(
                "image.model"
            ):
                digest = str(layer.get("digest") or "")
                break
        if not digest:
            continue
        blob = root / "blobs" / digest.replace(":", "-")
        if not blob.is_file() or str(blob) in seen:
            continue
        seen.add(str(blob))
        entry = _describe(blob, "ollama")
        if entry is None:
            continue
        # The blob's filename is a hash; the manifest path is the model tag
        # (`.../manifests/registry.ollama.ai/library/gemma4/e2b`).
        parts = manifest.parts
        tag = f"{parts[-2]}:{parts[-1]}" if len(parts) >= 2 else blob.name
        found.append(
            ModelFile(
                path=entry.path,
                origin="ollama",
                name=tag,
                size_bytes=entry.size_bytes,
                architecture=entry.architecture,
                parameters=entry.parameters,
                context_length=entry.context_length,
                quantization=entry.quantization,
                error=entry.error,
            )
        )
    return found


def _lmstudio_files() -> list[ModelFile]:
    """GGUFs LM Studio has indexed. Its models directory is user-configurable (on
    this machine it lives on another drive), so the index — not a directory guess —
    is what knows where they are."""
    index_path = gguf.lmstudio_index()
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = index.get("models")
    if not isinstance(entries, list):
        return []
    found: list[ModelFile] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("indexedModelIdentifier") or "")
        point = entry.get("entryPoint")
        path_str = point.get("absPath") if isinstance(point, dict) else None
        if not isinstance(path_str, str):
            continue
        path = Path(path_str)
        if not path.is_file() or str(path) in seen:
            continue
        seen.add(str(path))
        described = _describe(path, "lmstudio")
        if described is None:
            continue
        found.append(
            ModelFile(
                path=described.path,
                origin="lmstudio",
                name=identifier or described.name,
                size_bytes=described.size_bytes,
                architecture=described.architecture,
                parameters=described.parameters,
                context_length=described.context_length,
                quantization=described.quantization,
                error=described.error,
            )
        )
    return found


def list_models(include_external: bool = True) -> list[ModelFile]:
    """Every GGUF this node could serve, managed ones first."""
    models: list[ModelFile] = _scan(models_root(), "managed")
    for extra in extra_dirs():
        models.extend(_scan(extra, "extra"))
    if include_external:
        models.extend(_ollama_blobs())
        models.extend(_lmstudio_files())
    seen: set[str] = set()
    unique: list[ModelFile] = []
    for model in models:
        key = str(model.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(model)
    return unique


def find_model(path: str) -> ModelFile | None:
    target = Path(path).expanduser()
    for model in list_models():
        if model.path == target:
            return model
    return None


def is_managed(path: Path) -> bool:
    """True when `path` is inside the managed directory.

    Resolved on both sides: a `..` segment in a caller-supplied path is exactly how
    a delete route becomes an arbitrary-file-delete route.
    """
    try:
        return models_root().resolve() in path.resolve().parents
    except OSError:
        return False


def delete_model(path: str) -> None:
    target = Path(path).expanduser()
    if not is_managed(target):
        raise ValueError(
            "only models under the managed directory can be deleted here — "
            "Ollama and LM Studio own theirs"
        )
    if not target.is_file():
        raise FileNotFoundError(str(target))
    target.unlink()


def _sanitize(part: str) -> str:
    """A path segment safe on every OS, from a repo id or filename."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-.")
    return cleaned or "model"


def target_path(repo: str, filename: str) -> Path:
    owner, _, name = repo.partition("/")
    folder = f"{_sanitize(owner)}--{_sanitize(name)}" if name else _sanitize(owner)
    return models_root() / folder / _sanitize(Path(filename).name)


async def _hf_token() -> str | None:
    """The Hugging Face connector's token, when one is connected.

    Optional by design: the great majority of GGUF repos are public, so requiring
    a connected account to fetch weights would gate the common case on an OAuth
    flow nobody needs.
    """
    try:
        from backend.modules.connectors.providers.huggingface import token

        return await token()
    except Exception as exc:  # noqa: BLE001 — connector absence must not block a download
        logger.info("llamacpp: no Hugging Face token available (%s)", exc)
        return None


async def list_repo_ggufs(
    repo: str, *, client: httpx.AsyncClient | None = None
) -> list[dict[str, Any]]:
    """The GGUF files in a Hugging Face model repo, with their sizes.

    Sizes come from the tree API's `size` field, which is what makes the pre-flight
    budget check possible: a quantization picker that cannot say "this one is 4 GB
    and this one is 14 GB" is just a list of cryptic suffixes.
    """
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        headers = {}
        tok = await _hf_token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        res = await client.get(
            f"{HF_ENDPOINT}/api/models/{repo}/tree/main",
            params={"recursive": "1"},
            headers=headers,
        )
        res.raise_for_status()
        entries = res.json()
        files: list[dict[str, Any]] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("type") != "file":
                continue
            path = str(entry.get("path") or "")
            if not path.lower().endswith(".gguf"):
                continue
            files.append(
                {
                    "path": path,
                    "sizeBytes": int(entry.get("size") or 0),
                    "isProjector": Path(path).name.startswith("mmproj-"),
                }
            )
        files.sort(key=lambda f: f["path"])
        return files
    finally:
        if owns:
            await client.aclose()


async def download_model(
    repo: str, filename: str, *, client: httpx.AsyncClient | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Fetch one GGUF from Hugging Face into the managed directory.

    Written to a `.part` file and renamed on success, so an interrupted download
    can never be picked up by the catalog scan as a servable model — a truncated
    GGUF fails at load time with an error that reads like a corrupt *model*.
    """
    dest = target_path(repo, filename)
    if dest.exists():
        yield {"status": "done", "path": str(dest), "note": "already downloaded"}
        return

    owns = client is None
    client = client or httpx.AsyncClient(timeout=None, follow_redirects=True)
    try:
        headers: dict[str, str] = {}
        tok = await _hf_token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        url = f"{HF_ENDPOINT}/{repo}/resolve/main/{filename}"

        yield {"status": "resolving", "repo": repo, "file": filename}
        try:
            head = await client.head(url, headers=headers)
            head.raise_for_status()
            total = int(head.headers.get("content-length") or 0)
        except httpx.HTTPError as exc:
            yield {"error": f"could not reach {repo}/{filename}: {exc}"}
            return

        # The budget is checked *before* a byte is written, against the declared
        # size. Checking as we go would mean discovering the disk is full 30 GB in.
        budget = disk_budget_bytes()
        used = managed_bytes()
        if budget and total and used + total > budget:
            yield {
                "error": (
                    f"{_gb(total)} would take the managed model directory to "
                    f"{_gb(used + total)}, over the {_gb(budget)} budget. Delete a "
                    "model or raise llamacpp.diskBudgetGb."
                )
            }
            return

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        completed = 0
        yield {
            "status": "downloading",
            "total": total,
            "completed": 0,
            "file": filename,
        }
        try:
            async with client.stream("GET", url, headers=headers) as res:
                res.raise_for_status()
                with open(part, "wb") as handle:
                    async for chunk in res.aiter_bytes(1 << 22):
                        handle.write(chunk)
                        completed += len(chunk)
                        yield {
                            "status": "downloading",
                            "total": total,
                            "completed": completed,
                            "file": filename,
                        }
        except httpx.HTTPError as exc:
            part.unlink(missing_ok=True)
            yield {"error": f"download failed: {exc}"}
            return

        part.replace(dest)
        yield {"status": "done", "path": str(dest), "sizeBytes": completed}
    finally:
        if owns:
            await client.aclose()


def _gb(value: int) -> str:
    return f"{value / 1024**3:.1f} GB"


def usage() -> dict[str, Any]:
    used = managed_bytes()
    budget = disk_budget_bytes()
    return {
        "usedBytes": used,
        "budgetBytes": budget,
        "root": str(models_root()),
        "extraDirs": [str(p) for p in extra_dirs()],
    }


def suggested_repos() -> Iterable[dict[str, str]]:
    """A short starter list for the download form.

    Not a catalog and not a ranking — three small, openly-licensed, tool-calling
    models that fit on a laptop, so the first-run experience is not an empty text
    box demanding a repo id the user has no way to guess.
    """
    return (
        {
            "repo": "unsloth/gemma-3-4b-it-GGUF",
            "label": "Gemma 3 4B Instruct",
            "note": "~2.5 GB at Q4 — a good default on a CPU-only machine.",
        },
        {
            "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
            "label": "Qwen2.5 7B Instruct",
            "note": "Strong tool calling for its size; ~4.7 GB at Q4.",
        },
        {
            "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
            "label": "Llama 3.2 3B Instruct",
            "note": "~2 GB at Q4 — the smallest here that still holds a tool loop.",
        },
    )
