"""Install a downloaded Jacobian / J++ lens into the llama.cpp lens slot.

Neuronpedia publishes fitted lenses as `torch.save` files, one per Hugging Face
model; `llamacpp/lens.py` reads `$DATA/llamacpp/lens/<gguf sha>/<lens>/J_<l>.npy`.
Between the two: the worker converts (it has torch; the backend does not), and the
install is per GGUF file, because the lens slot is keyed by the file the trace ran.

A lens trained on one model and installed for another would still produce words —
confident, and wrong — so targets are only the local GGUFs the compatibility check
calls compatible. `unknown` is not enough here: a lens file states no width, so
every unrelated GGUF (Whisper, a TTS model) is merely not *contradicted*; offering
them all would be a list of ways to get a wrong reading.
"""

from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from typing import Any

from backend.modules.interp import compat
from backend.modules.interp.worker import WorkerUnavailable, worker


class LensInstallError(Exception):
    """The lens cannot be installed as asked — a reason for the reader."""


def lens_files(artifact: dict[str, Any]) -> list[Path]:
    """The lens files an artifact holds: J-lens and/or J++ (`*.pt`)."""
    root = Path(artifact["path"])
    return sorted(root.rglob("*.pt")) if root.is_dir() else []


def lens_id(file: Path) -> str:
    """A stable slot name: `np-jlens-<dataset>` / `np-jpp-<dataset>`."""
    kind = (
        "jpp" if "_jpp_" in file.name or file.parent.parent.name == "jpp" else "jlens"
    )
    dataset = file.parent.name.lower().replace("_", "-")
    return f"np-{kind}-{dataset}"


def lens_label(file: Path, n_prompts: int | None = None) -> str:
    kind = "J++ lens" if lens_id(file).startswith("np-jpp") else "Jacobian lens"
    return f"{kind} (Neuronpedia, {file.parent.name})" + (
        f", {n_prompts} prompts" if n_prompts else ""
    )


@functools.lru_cache(maxsize=256)
def _target(path: str, mtime: int) -> dict[str, Any] | None:
    from backend.modules.interpretability.gguf import read_header

    try:
        header = read_header(path)
    except Exception:  # noqa: BLE001 — an unreadable GGUF is simply not a target
        return None
    return compat.gguf_target(header.metadata, path)


def targets(artifact: dict[str, Any]) -> dict[str, Any]:
    """Local GGUFs this lens fits, and how many others were left out."""
    from backend.modules.llamacpp import catalog, lens, traces

    out: list[dict[str, Any]] = []
    unmatched = 0
    for model in catalog.list_models():
        if model.error:
            continue
        try:
            mtime = int(model.path.stat().st_mtime)
        except OSError:
            continue
        target = _target(str(model.path), mtime)
        if target is None:
            continue
        verdict = compat.check(artifact, target)
        if verdict["state"] != "compatible":
            unmatched += 1
            continue
        sha = traces.model_sha(model.path)
        installed = {spec.id for spec in lens.available_lenses(sha)}
        out.append(
            {
                "modelPath": str(model.path),
                "name": model.name,
                "compat": verdict,
                "installed": [
                    lens_id(f) for f in lens_files(artifact) if lens_id(f) in installed
                ],
            }
        )
    out.sort(key=lambda t: t["name"])
    return {"targets": out, "unmatched": unmatched}


async def install(artifact: dict[str, Any], model_path: str) -> list[dict[str, Any]]:
    """Convert every lens file of `artifact` into the slot for `model_path`."""
    from backend.modules.llamacpp import lens, traces

    path = Path(model_path)
    if not path.is_file():
        raise LensInstallError(f"no GGUF at {model_path}")
    target = _target(str(path), int(path.stat().st_mtime))
    verdict = compat.check(artifact, target)
    if verdict["state"] != "compatible":
        raise LensInstallError(
            "this lens does not match that GGUF ("
            + ("; ".join(verdict["reasons"]) or verdict["state"])
            + ") — a lens on the wrong model still reads out words, all wrong"
        )
    files = lens_files(artifact)
    if not files:
        raise LensInstallError(f"{artifact['id']} holds no lens files")
    if worker.state() != "ready":
        from backend.modules.interp.artifacts import _token

        try:
            await asyncio.to_thread(worker.start, await _token())
        except WorkerUnavailable as exc:
            raise LensInstallError(f"the worker cannot run: {exc}") from exc

    root = lens.lens_root() / traces.model_sha(path)
    installed: list[dict[str, Any]] = []
    for file in files:
        config = file.parent / "config.yaml"
        provenance = (
            f"{artifact['repo']}/{file.relative_to(Path(artifact['path']).parent).as_posix()}; "
            f"fitted on {artifact.get('modelRepo')}; installed for {path.name} "
            f"(matched: {verdict['state']}{', ' + verdict['reasons'][0] if verdict['reasons'] else ''})"
        )
        reply = await asyncio.to_thread(
            worker.request,
            "jlens_convert",
            {
                "src": str(file),
                "dest": str(root / lens_id(file)),
                "label": lens_label(file),
                "provenance": provenance,
            },
            600,
        )
        if reply.get("state") != "ran":
            raise LensInstallError(
                reply.get("reason") or reply.get("traceback") or "the conversion failed"
            )
        result = reply["result"]
        manifest_path = root / lens_id(file) / "lens.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["label"] = lens_label(file, result.get("nPrompts"))
        if config.is_file():
            manifest["config"] = config.read_text(encoding="utf-8")[:4000]
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        installed.append({"lensId": lens_id(file), **result})
    return installed
