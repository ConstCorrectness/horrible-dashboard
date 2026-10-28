"""The interpretability worker: a long-lived process that holds one Hugging Face model.

Run by `worker.py` with the `$DATA/interp-venv` interpreter, **never imported by the
backend** — it imports torch, which the backend does not have. Self-contained for the
same reason the designer's probe is: it must not depend on anything of ours that the
venv does not carry.

Protocol: JSON lines. One request per line on stdin, `{"id", "op", "args"}`; one
reply per line on stdout, `{"id", "state", ...}` where `state` is

- `ran` — with `result`;
- `failed` — with `traceback`, verbatim (the traceback *is* the answer);
- `not_checked` — with `reason`: it was not attempted, and why (no CUDA, a model
  that is not cached and would have to be downloaded, nothing loaded).

stdout carries nothing else. Libraries print freely (transformers' progress bars,
warnings), so `sys.stdout` is pointed at stderr and the protocol keeps its own handle.

Ops are plain functions in `OPS`. Later tools add theirs (SAE encoding, steering,
attribution) as new entries; the protocol does not change.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Any, Callable

PROTOCOL = sys.stdout
sys.stdout = sys.stderr

# The one model this worker holds. Loading a second replaces the first: a 12 GB card
# fits one small model, and two half-loaded ones would be worse than either.
_held: dict[str, Any] = {}


class NotChecked(Exception):
    """Raised by an op that declines rather than fails."""


def _torch():
    import torch

    return torch


def op_ping(_: dict[str, Any]) -> dict[str, Any]:
    torch = _torch()
    import transformers

    cuda = torch.cuda.is_available()
    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cuda": cuda,
    }
    if cuda:
        props = torch.cuda.get_device_properties(0)
        info.update(device=props.name, vramBytes=int(props.total_memory))
    return info


def op_status(_: dict[str, Any]) -> dict[str, Any]:
    torch = _torch()
    out: dict[str, Any] = {"loaded": None}
    if _held:
        out["loaded"] = {
            k: v
            for k, v in _held.items()
            if k in ("repo", "dtype", "device", "layers", "dModel", "loadSeconds")
        }
    if torch.cuda.is_available():
        out["vramAllocated"] = int(torch.cuda.memory_allocated())
        out["vramReserved"] = int(torch.cuda.memory_reserved())
    return out


def _cached(repo: str) -> bool:
    from huggingface_hub import scan_cache_dir

    try:
        return any(
            r.repo_id == repo and r.repo_type == "model" for r in scan_cache_dir().repos
        )
    except Exception:  # noqa: BLE001 — an unreadable cache is "not cached"
        return False


def op_load(args: dict[str, Any]) -> dict[str, Any]:
    """Load `repo` from the Hugging Face cache. Downloading is opt-in
    (`allowDownload`): a model that is not cached can be many gigabytes."""
    torch = _torch()
    from transformers import AutoModelForCausalLM, AutoTokenizer

    repo = str(args["repo"])
    if not args.get("allowDownload") and not _cached(repo):
        raise NotChecked(
            f"{repo} is not in the Hugging Face cache; loading it would download it. "
            "Pass allowDownload once the size has been confirmed."
        )
    dtype_name = str(args.get("dtype") or "bfloat16")
    dtype = getattr(torch, dtype_name)
    device = (
        "cuda"
        if torch.cuda.is_available() and args.get("device", "auto") != "cpu"
        else "cpu"
    )
    op_unload({})
    started = time.time()
    tokenizer = AutoTokenizer.from_pretrained(
        repo, local_files_only=not args.get("allowDownload")
    )
    model = AutoModelForCausalLM.from_pretrained(
        repo,
        dtype=dtype,
        local_files_only=not args.get("allowDownload"),
    ).to(device)
    model.eval()
    cfg = model.config
    _held.update(
        repo=repo,
        dtype=dtype_name,
        device=device,
        model=model,
        tokenizer=tokenizer,
        layers=int(getattr(cfg, "num_hidden_layers", 0) or 0),
        dModel=int(getattr(cfg, "hidden_size", 0) or 0),
        loadSeconds=round(time.time() - started, 2),
    )
    return op_status({})


def op_unload(_: dict[str, Any]) -> dict[str, Any]:
    if _held:
        _held.clear()
        import gc

        gc.collect()
        torch = _torch()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return {"loaded": None}


def op_generate(args: dict[str, Any]) -> dict[str, Any]:
    """Greedy continuation of `prompt` — proof the held model runs, and the
    baseline a steered generation is later compared against."""
    if not _held:
        raise NotChecked("no model is loaded")
    torch = _torch()
    tokenizer, model = _held["tokenizer"], _held["model"]
    max_new = max(1, min(int(args.get("maxNewTokens") or 32), 256))
    inputs = tokenizer(str(args.get("prompt") or ""), return_tensors="pt").to(
        _held["device"]
    )
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    new = out[0][inputs["input_ids"].shape[1] :]
    return {
        "text": tokenizer.decode(new, skip_special_tokens=True),
        "tokens": int(new.shape[0]),
    }


def op_jlens_convert(args: dict[str, Any]) -> dict[str, Any]:
    """Convert a Neuronpedia Jacobian / J++ lens (`torch.save` dict) into the slot
    `llamacpp/lens.py` reads: one `J_<layer>.npy` (fp16) per layer + `lens.json`.

    `weights_only=True`: the file is a pickle from the internet, and this refuses
    anything but tensors and plain containers rather than running its code.

    Layer `k` of the file is the residual after block `k` — llama.cpp's `l_out-k`.
    Measured, not assumed (docs/modules/interp-suite.mdx): read that way, the
    Gemma 3 1B lens surfaces two-hop bridge concepts tens of layers early; read as
    block inputs it does slightly worse, and transposed it reads nothing.
    """
    import json as _json
    from pathlib import Path as _Path

    import numpy as np

    torch = _torch()
    src, dest = _Path(str(args["src"])), _Path(str(args["dest"]))
    if not src.is_file():
        raise NotChecked(f"no lens file at {src}")
    data = torch.load(src, map_location="cpu", weights_only=True)
    maps = data.get("J") if isinstance(data, dict) else None
    if not isinstance(maps, dict) or not maps:
        raise NotChecked(f"{src.name} holds no `J` maps — not a Jacobian lens file")
    d_model = int(data.get("d_model") or 0)
    dest.mkdir(parents=True, exist_ok=True)
    layers: list[int] = []
    for key, matrix in maps.items():
        layer = int(key)
        m = matrix.to(torch.float16).cpu().numpy()
        if (
            m.ndim != 2
            or m.shape[0] != m.shape[1]
            or (d_model and m.shape[0] != d_model)
        ):
            raise NotChecked(
                f"J[{layer}] is {tuple(m.shape)}, not a {d_model}x{d_model} map"
            )
        np.save(dest / f"J_{layer}.npy", m)
        layers.append(layer)
    provenance = data.get("provenance")
    manifest = {
        "kind": "jacobian",
        "label": str(args.get("label") or src.stem),
        "provenance": str(args.get("provenance") or ""),
        "fileProvenance": provenance if isinstance(provenance, (dict, str)) else None,
        "layers": sorted(layers),
        "dModel": d_model or int(next(iter(maps.values())).shape[0]),
        "nPrompts": int(data.get("n_prompts") or 0),
        "source": str(src),
        "alignment": "J[k] reads l_out-k, the residual after block k",
    }
    (dest / "lens.json").write_text(_json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "dest": str(dest),
        "layers": manifest["layers"],
        "dModel": manifest["dModel"],
        "nPrompts": manifest["nPrompts"],
    }


OPS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ping": op_ping,
    "status": op_status,
    "load": op_load,
    "unload": op_unload,
    "generate": op_generate,
    "jlens_convert": op_jlens_convert,
}


def _send(message: dict[str, Any]) -> None:
    PROTOCOL.write(json.dumps(message, default=str) + "\n")
    PROTOCOL.flush()


def main() -> None:
    _send({"event": "ready", "pid": os.getpid(), "ops": sorted(OPS)})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _send(
                {"id": None, "state": "failed", "traceback": f"not JSON: {line[:200]}"}
            )
            continue
        rid = request.get("id")
        op = OPS.get(str(request.get("op")))
        if op is None:
            _send(
                {
                    "id": rid,
                    "state": "not_checked",
                    "reason": f"unknown op {request.get('op')!r}",
                }
            )
            continue
        try:
            _send({"id": rid, "state": "ran", "result": op(request.get("args") or {})})
        except NotChecked as exc:
            _send({"id": rid, "state": "not_checked", "reason": str(exc)})
        except Exception:  # noqa: BLE001 — the traceback is the answer
            _send({"id": rid, "state": "failed", "traceback": traceback.format_exc()})


if __name__ == "__main__":
    main()
