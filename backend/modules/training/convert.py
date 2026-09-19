"""Closing the loop: a checkpoint you trained becomes a GGUF this node serves.

Without this, "train a model" and "run a model" are two apps that happen to be
in the same window. With it, a run that finishes under `outputs/` converts into
the llama.cpp module's **managed** model directory, appears in its catalog, and
is served by `llama-server` — so *train it → look inside it* happens in one app.

Three things make this less obvious than "run a script":

- **The converter is not in the release binaries.** `llama-server` ships as a
  compiled binary; `convert_hf_to_gguf.py` lives in the llama.cpp *repo*. So it
  is fetched on demand from GitHub **at the tag of the build that is installed**,
  cached under the data dir, and its sha256 recorded — a converter from a
  different release than the runtime is exactly the kind of mismatch that
  produces a file which loads and is subtly wrong. It is **not one file**: the
  script imports a sibling `conversion/` package (one module per architecture)
  and prefers the repo's own `gguf-py` over the PyPI `gguf`, so the tag's source
  archive is fetched and exactly those parts extracted. Fetching the lone script
  — which this once did — fails every conversion with `No module named
  'conversion'`. GitHub publishes no digest for an archive, so the record says
  `verified: false` rather than implying we checked it against anything.
- **A LoRA checkpoint is not a model.** A PEFT run writes `adapter_config.json`
  and a few megabytes of adapter — feeding that to the base converter fails with
  an error about missing weights that reads like a corrupt checkpoint. The two
  are told apart by that file and sent to different converters
  (`convert_lora_to_gguf.py`), and a LoRA GGUF is labelled as an adapter, since
  it is loaded with `--lora` beside a base model rather than served alone.
- **It runs in the project venv.** The converter imports torch, transformers and
  numpy; the backend env has none of them and must not grow them. Same
  subprocess-on-a-thread shape as everything else here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import httpx

from backend.modules.training import envs, lineage
from backend.modules.training.envs import python_path, venv_dir, venv_ready
from backend.modules.training.models import ProjectModel
from backend import paths

logger = logging.getLogger(__name__)

# `tar.gz/<ref>` takes a tag or a branch, so the `master` fallback works too.
ARCHIVE_BASE = "https://codeload.github.com/ggml-org/llama.cpp/tar.gz"

#: What a converter needs from the repo, relative to its root: the scripts
#: themselves (the LoRA one imports the HF one), the per-architecture package the
#: HF one imports, and the `gguf-py` it puts ahead of the installed `gguf`.
CONVERTER_PARTS = (
    "convert_hf_to_gguf.py",
    "convert_lora_to_gguf.py",
    "conversion/",
    "gguf-py/",
)

#: Written last into a converter directory; its absence means "incomplete".
COMPLETE_MARKER = "converter.json"

#: Quantizations the converter itself can write. Anything smaller is a second
#: step through `llama-quantize`, which this deliberately does not wrap: offering
#: `q4_k_m` here and silently producing `f16` would be worse than not offering it.
OUTPUT_TYPES = ("f16", "bf16", "f32", "q8_0")

CONVERTERS = {
    "model": "convert_hf_to_gguf.py",
    "lora": "convert_lora_to_gguf.py",
}


def scripts_dir() -> Path:
    return paths.data_dir() / "llamacpp" / "convert"


def checkpoint_kind(path: Path) -> str:
    """`lora` for a PEFT adapter directory, `model` for a full checkpoint.

    Decided by `adapter_config.json`, which is the file PEFT writes and a full
    save never does — not by directory size or file names, both of which vary by
    trainer version.
    """
    return "lora" if (path / "adapter_config.json").is_file() else "model"


def list_checkpoints(project: ProjectModel) -> list[dict[str, Any]]:
    """Directories under the project that look like something to convert.

    A checkpoint is a directory with a `config.json` (full model) or an
    `adapter_config.json` (adapter). Walking for weights files instead would list
    every `.safetensors` in a dataset cache.
    """
    root = Path(project.root)
    found: list[dict[str, Any]] = []
    for marker in ("config.json", "adapter_config.json"):
        for path in sorted(root.rglob(marker)):
            directory = path.parent
            # A venv or a HF cache under the project is not this project's output.
            if any(
                part in (".venv", ".git", "__pycache__", ".cache")
                for part in directory.relative_to(root).parts
            ):
                continue
            entry = {
                "path": str(directory),
                "relPath": str(directory.relative_to(root)).replace("\\", "/"),
                "kind": checkpoint_kind(directory),
                "sizeBytes": _dir_size(directory),
                "modified": _mtime(directory),
            }
            if entry not in found:
                found.append(entry)
    found.sort(key=lambda e: e["modified"], reverse=True)
    return found


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# --- the converter script -----------------------------------------------------


def installed_tag() -> str:
    """The tag of the `llama-server` build this node has, or `master`.

    Matching the runtime is the point: a converter from a different release can
    write a file the installed server cannot read, or — worse — one it reads
    slightly wrong.
    """
    try:
        from backend.modules.llamacpp import binaries

        install = binaries.newest_install()
        if install is not None:
            return install.tag
    except Exception as exc:  # noqa: BLE001 — no build installed is a fine answer
        logger.info("training: no llama.cpp build to match a converter to (%s)", exc)
    return "master"


def _wanted(name: str) -> str | None:
    """The repo-relative path for an archive member we keep, else None.

    Archive members are `llama.cpp-<tag>/<path>`; the prefix is dropped, and
    anything that is not a regular path under a wanted part is refused — which
    is also what keeps `..` or an absolute name from escaping the directory.
    """
    _, _, rel = name.partition("/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    for part in CONVERTER_PARTS:
        if rel == part or (part.endswith("/") and rel.startswith(part)):
            return rel
    return None


def _extract_converter(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            rel = _wanted(member.name)
            if rel is None or not member.isfile():
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with source, out.open("wb") as sink:
                shutil.copyfileobj(source, sink)
    for part in CONVERTER_PARTS:
        if not (dest / part.rstrip("/")).exists():
            raise FileNotFoundError(f"the llama.cpp archive has no {part}")


async def ensure_converter(kind: str, tag: str) -> Path:
    """The converter script for `kind` at `tag`, fetched once and cached.

    The whole converter tree lands in a temporary directory that is renamed into
    place only once it is complete, so an interrupted fetch can never be executed
    as a half-extracted package. A directory without the marker — including the
    single-script layout an earlier version cached — is replaced.
    """
    name = CONVERTERS[kind]
    target_dir = scripts_dir() / tag
    target = target_dir / name
    if target.is_file() and (target_dir / COMPLETE_MARKER).is_file():
        return target
    scripts_dir().mkdir(parents=True, exist_ok=True)
    url = f"{ARCHIVE_BASE}/{tag}"
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(dir=scripts_dir(), prefix=f".{tag}-") as tmp:
        archive = Path(tmp) / "src.tar.gz"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", url) as res:
                res.raise_for_status()
                with archive.open("wb") as sink:
                    async for chunk in res.aiter_bytes():
                        digest.update(chunk)
                        sink.write(chunk)
        staged = Path(tmp) / "tree"
        await asyncio.to_thread(_extract_converter, archive, staged)
        (staged / COMPLETE_MARKER).write_text(
            json.dumps(
                {
                    "url": url,
                    "tag": tag,
                    "sha256": digest.hexdigest(),
                    "parts": list(CONVERTER_PARTS),
                    # GitHub publishes no digest for a source archive, so this
                    # records what arrived rather than claiming it matched.
                    "verified": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if target_dir.exists():
            shutil.rmtree(target_dir)
        staged.replace(target_dir)
    return target


def _output_path(
    project: ProjectModel, checkpoint: Path, kind: str, out_type: str
) -> Path:
    """Where the GGUF lands: the llama.cpp module's **managed** directory.

    Managed and not the project directory, because managed is the one the catalog
    scans, the disk budget counts, and the delete route is allowed to touch.
    """
    from backend.modules.llamacpp import catalog

    stem = f"{project.id}-{checkpoint.name}".strip("-") or project.id
    suffix = "-lora" if kind == "lora" else ""
    return catalog.models_root() / "trained" / f"{stem}{suffix}-{out_type}.gguf"


async def run_conversion(
    project: ProjectModel,
    checkpoint: str,
    *,
    out_type: str = "f16",
    base_model: str = "",
) -> Any:
    """Convert one checkpoint, yielding NDJSON-shaped progress events."""
    root = Path(project.root).resolve()
    target = (
        (root / checkpoint).resolve()
        if not Path(checkpoint).is_absolute()
        else Path(checkpoint).resolve()
    )
    if not target.is_relative_to(root):
        yield {"error": f"checkpoint escapes the project root: {checkpoint}"}
        return
    if not target.is_dir():
        yield {"error": f"no such checkpoint: {checkpoint}"}
        return
    if out_type not in OUTPUT_TYPES:
        yield {"error": f"unsupported output type {out_type}"}
        return
    if not venv_ready(project):
        yield {"error": "this project's venv is not ready — the converter needs torch"}
        return

    kind = checkpoint_kind(target)
    if kind == "lora" and not base_model:
        # The adapter alone doesn't say what it adapts in a form the converter can
        # use, and guessing produces an adapter GGUF that silently doesn't match.
        base = _base_from_adapter(target)
        if not base:
            yield {
                "error": (
                    "this is a LoRA adapter and its base model could not be read "
                    "from adapter_config.json — pass one explicitly"
                )
            }
            return
        base_model = base

    missing = _missing_deps(project)
    if missing:
        # Installed rather than merely reported: the manim runner sets the
        # precedent (it installs manim into the venv on first render), and
        # "convert failed: No module named gguf" is a dead end for anyone who
        # doesn't already know it lives on PyPI separately from llama.cpp.
        yield {"status": "installing", "packages": missing}
        # Through `envs.install`, which is `uv pip install --python <venv>`. Not
        # `python -m pip`: a uv-created venv has no pip in it at all, so the
        # obvious spelling fails with "No module named pip" on every project this
        # module makes.
        lines: list[str] = []
        try:
            await asyncio.to_thread(envs.install, project, missing, lines.append)
        except Exception as exc:  # noqa: BLE001 — ProviderError and OSError both land here
            yield {
                "error": f"could not install {', '.join(missing)} into the project venv: {exc}",
                "log": lines[-40:],
            }
            return

    tag = installed_tag()
    yield {"status": "fetching converter", "tag": tag, "kind": kind}
    try:
        script = await ensure_converter(kind, tag)
    except httpx.HTTPError as exc:
        yield {"error": f"could not fetch the converter for {tag}: {exc}"}
        return

    out_path = _output_path(project, target, kind, out_type)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python_path(project)),
        str(script),
        str(target),
        "--outfile",
        str(out_path),
        "--outtype",
        out_type,
    ]
    if kind == "lora":
        cmd += _base_args(base_model)

    yield {"status": "converting", "outfile": str(out_path), "kind": kind}
    code, tail = await _run(cmd, cwd=str(root))
    if code != 0:
        out_path.unlink(missing_ok=True)
        yield {
            "error": f"the converter exited with code {code}",
            "log": tail,
        }
        return
    if not out_path.is_file():
        yield {"error": "the converter reported success but wrote no file", "log": tail}
        return
    snapshot = _recipe_snapshot(project)
    # Record where this file came from, at the one moment every field is known.
    # Wrapped inside `lineage.record`, which never raises: the GGUF is on disk and
    # servable whether or not the bookkeeping row lands.
    lineage.record(
        str(out_path),
        project_id=project.id,
        checkpoint=target.name,
        # `base_model` is only *required* for a LoRA (the adapter is meaningless
        # without it), so for a full fine-tune the argument is empty and the recipe
        # is the only place the base is recorded. Falling back to it is what makes
        # "score this against its base" work for the common case — reading the
        # argument alone would leave every full fine-tune with no base at all.
        base_model=base_model or snapshot.get("baseModel", ""),
        out_type=out_type,
        is_adapter=kind == "lora",
        recipe=snapshot,
    )

    yield {
        "status": "done",
        "path": str(out_path),
        "sizeBytes": out_path.stat().st_size,
        "kind": kind,
        # An adapter is not servable on its own — the pane says so rather than
        # offering a "serve this" button that produces nonsense.
        "servable": kind == "model",
        "log": tail,
    }


def _recipe_snapshot(project: ProjectModel) -> dict[str, Any]:
    """The recipe as it stood at conversion time.

    A snapshot rather than a pointer to `recipe.json`: that file keeps changing as
    the user tunes the next run, and a lineage row saying "trained with lr=2e-4"
    must keep meaning that after they try 1e-4. Best-effort — a project whose
    recipe was never saved converts perfectly well.
    """
    try:
        from backend.modules.training.recipes import load_recipe

        recipe = load_recipe(project)
        return {
            "baseModel": recipe.base_model,
            "dataset": recipe.dataset,
            "datasetSplit": recipe.dataset_split,
            "useLora": recipe.use_lora,
            "values": dict(recipe.values),
        }
    except Exception:  # noqa: BLE001 — provenance detail, never a conversion failure
        logger.debug("training: no recipe snapshot for %s", project.id, exc_info=True)
        return {}


#: What the converter imports beyond what a training venv already has. `gguf` is
#: its own PyPI package, published by the llama.cpp project and versioned
#: independently of the binaries — which is why it can't just be assumed present.
CONVERTER_DEPS = ("gguf", "sentencepiece", "protobuf")


def _missing_deps(project: ProjectModel) -> list[str]:
    """Which converter dependencies the project venv lacks.

    Read from `dist-info` on disk rather than by importing them in a subprocess:
    one spawn per check, on every conversion, to learn something the filesystem
    already knows.
    """
    venv = venv_dir(project)
    roots = [venv / "Lib" / "site-packages", *venv.glob("lib/python*/site-packages")]
    present: set[str] = set()
    for directory in roots:
        if not directory.is_dir():
            continue
        for item in directory.glob("*.dist-info"):
            present.add(item.name.split("-")[0].replace("_", "-").lower())
    return [dep for dep in CONVERTER_DEPS if dep not in present]


def _base_from_adapter(path: Path) -> str:
    try:
        data = json.loads((path / "adapter_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("base_model_name_or_path") or "")


def _base_args(base_model: str) -> list[str]:
    """How to tell `convert_lora_to_gguf.py` which model the adapter adapts.

    Its `--base` is a `Path` to a local model directory: handed a Hub id such as
    `Qwen/Qwen3-0.6B` — which is what `adapter_config.json` records for every
    adapter trained from the Hub — it looks for that directory, finds nothing, and
    exits. A Hub id goes to `--base-model-id`, which fetches only the config.
    """
    if Path(base_model).expanduser().is_dir():
        return ["--base", str(Path(base_model).expanduser())]
    return ["--base-model-id", base_model]


async def _run(cmd: list[str], *, cwd: str) -> tuple[int, list[str]]:
    """Run the converter on a worker thread, keeping the last lines of output.

    Blocking `Popen` offloaded with `to_thread`, never
    `asyncio.create_subprocess_exec` — under `uvicorn --reload` on Windows the
    loop is a `SelectorEventLoop` and cannot spawn subprocesses at all.
    """

    def work() -> tuple[int, list[str]]:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip())
            del lines[:-40]  # a conversion prints one line per tensor
        return proc.wait(), lines

    return await asyncio.to_thread(work)


def python_version_note() -> str:
    """Why a conversion can fail before it starts, on this machine specifically."""
    return (
        "The converter runs in the project venv, so torch, transformers and gguf "
        f"must be installed there (this backend runs {sys.version.split()[0]} and "
        "deliberately has none of them)."
    )
