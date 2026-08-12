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
  produces a file which loads and is subtly wrong. It is the same on-demand
  fetch as the binary itself; GitHub publishes no digest for a raw file, so the
  record says `verified: false` rather than implying we checked it against
  anything.
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
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from backend.modules.training import envs
from backend.modules.training.envs import python_path, venv_dir, venv_ready
from backend.modules.training.models import ProjectModel

logger = logging.getLogger(__name__)

RAW_BASE = "https://raw.githubusercontent.com/ggml-org/llama.cpp"

#: Quantizations the converter itself can write. Anything smaller is a second
#: step through `llama-quantize`, which this deliberately does not wrap: offering
#: `q4_k_m` here and silently producing `f16` would be worse than not offering it.
OUTPUT_TYPES = ("f16", "bf16", "f32", "q8_0")

CONVERTERS = {
    "model": "convert_hf_to_gguf.py",
    "lora": "convert_lora_to_gguf.py",
}


def scripts_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "llamacpp" / "convert"


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


async def ensure_converter(kind: str, tag: str) -> Path:
    """The converter script for `kind` at `tag`, fetched once and cached.

    Written via `.part` + rename like every other download here, so an
    interrupted fetch can never be executed as a truncated script.
    """
    name = CONVERTERS[kind]
    target = scripts_dir() / tag / name
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{RAW_BASE}/{tag}/{name}"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
        body = res.content
    part = target.with_suffix(target.suffix + ".part")
    part.write_bytes(body)
    part.replace(target)
    (target.parent / f"{name}.json").write_text(
        json.dumps(
            {
                "url": url,
                "tag": tag,
                "sha256": hashlib.sha256(body).hexdigest(),
                # GitHub publishes no digest for a raw file, so this records what
                # arrived rather than claiming it matched something.
                "verified": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
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
        cmd += ["--base", base_model]

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
