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


OPS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ping": op_ping,
    "status": op_status,
    "load": op_load,
    "unload": op_unload,
    "generate": op_generate,
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
