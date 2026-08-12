"""horrible-train: stream training telemetry from any Python process to
horrible-dashboard.

Works anywhere the dashboard supervises the process — a notebook kernel cell, a
`training.start_run` script, a manim render. Emission is a stdout **sentinel
protocol**: each event is one line, `@@HORRIBLE@@{json}`, which the backend strips
from the visible output and fans out to the UI panes. Zero hard dependencies;
numpy/PIL/torch are used only if the calling code already has them.

    import horrible_train as ht

    ht.run("baseline")                # optional: name the run (auto-created otherwise)
    ht.log(step=i, loss=0.42)         # -> live chart pane
    ht.frame(env.render())            # -> live rollout pane
    ht.watch(model, example=x)        # -> architecture pane (torch)
"""

from __future__ import annotations

import base64
import io
import json
import sys
import time
import uuid
from typing import Any

SENTINEL = "@@HORRIBLE@@"

_current_run: str | None = None
_watched: list[Any] = []  # [(model, weights_flag)] for stats re-emission on log()


def _emit(payload: dict[str, Any]) -> None:
    # One line, flushed immediately so long-running loops stream in real time.
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()


def run(name: str | None = None) -> str:
    """Start (or rename) the current run; returns its id. Called implicitly by the
    first `log()` if you don't call it yourself."""
    global _current_run
    _current_run = uuid.uuid4().hex[:8]
    _emit(
        {
            "type": "run",
            "runId": _current_run,
            "name": name or _current_run,
            "ts": time.time(),
        }
    )
    return _current_run


def log(step: int | None = None, **values: float) -> None:
    """Log scalar metrics for the current run: `ht.log(step=i, loss=0.4, acc=0.9)`."""
    if _current_run is None:
        run()
    clean = {k: float(v) for k, v in values.items()}
    _emit(
        {
            "type": "metric",
            "runId": _current_run,
            "step": step,
            "values": clean,
            "ts": time.time(),
        }
    )
    for model, weights in _watched:
        if weights:
            _emit_stats(model)


def frame(img: Any, source: str = "gym") -> None:
    """Stream one rendered frame (HxWx3 uint8 array, PIL image, or encoded bytes)
    to the rollout pane."""
    encoded, mime = _encode_image(img)
    if encoded is None:
        return
    _emit(
        {
            "type": "frame",
            "runId": _current_run,
            "source": source,
            "dataUrl": f"data:{mime};base64,{encoded}",
            "ts": time.time(),
        }
    )


def watch(model: Any, example: Any = None, weights: bool = False) -> None:
    """Publish a torch model's architecture to the model-graph pane. With
    `weights=True`, per-layer weight/grad norms ride along with every `log()`."""
    graph = _extract_graph(model, example)
    _watched.append((model, weights))
    _emit({"type": "model_graph", "graph": graph, "ts": time.time()})
    if weights:
        _emit_stats(model)


# --- internals -------------------------------------------------------------


def _encode_image(img: Any) -> tuple[str | None, str]:
    if isinstance(img, (bytes, bytearray)):
        return base64.b64encode(bytes(img)).decode("ascii"), "image/png"
    # PIL image (has .save) — preferred when available.
    if hasattr(img, "save") and hasattr(img, "mode"):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    # ndarray-like (HxWx3 uint8). Try PIL first, else a minimal stdlib PNG.
    if hasattr(img, "shape") and hasattr(img, "tobytes"):
        try:
            from PIL import Image

            buf = io.BytesIO()
            Image.fromarray(img).save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
        except ImportError:
            png = _png_rgb(img)
            if png is not None:
                return base64.b64encode(png).decode("ascii"), "image/png"
    return None, ""


def _png_rgb(arr: Any) -> bytes | None:
    """Encode an HxWx3 uint8 array as PNG with only the stdlib (zlib)."""
    import struct
    import zlib

    shape = tuple(getattr(arr, "shape", ()))
    if len(shape) != 3 or shape[2] != 3:
        return None
    h, w = int(shape[0]), int(shape[1])
    raw = arr.tobytes()
    if len(raw) != h * w * 3:
        return None
    stride = w * 3
    scanlines = b"".join(b"\x00" + raw[y * stride : (y + 1) * stride] for y in range(h))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(scanlines, 6))
        + chunk(b"IEND", b"")
    )


def _extract_graph(model: Any, example: Any) -> dict[str, Any]:
    """torch.fx graph when traceable (with shapes if an example input is given),
    else the named_modules containment tree — never fails."""
    try:
        return _fx_graph(model, example)
    except Exception:
        return _module_tree(model)


def _fx_graph(model: Any, example: Any) -> dict[str, Any]:
    import torch.fx

    traced = torch.fx.symbolic_trace(model)
    if example is not None:
        try:
            from torch.fx.passes.shape_prop import ShapeProp

            ShapeProp(traced).propagate(example)
        except Exception:
            pass  # shapes are decoration; the topology is still worth showing
    modules = dict(model.named_modules())
    nodes = []
    edges = []
    # call_module nodes are keyed by their *module path* so per-layer stats
    # (emitted per named_modules name) land on the right graph node.
    node_ids: dict[str, str] = {}
    for node in traced.graph.nodes:
        if node.op == "call_module" and str(node.target) in modules:
            node_ids[node.name] = str(node.target)
        else:
            node_ids[node.name] = node.name
    for node in traced.graph.nodes:
        op = node.op
        detail = str(node.target)
        params = 0
        if op == "call_module" and str(node.target) in modules:
            sub = modules[str(node.target)]
            detail = type(sub).__name__
            params = sum(p.numel() for p in sub.parameters(recurse=False))
        shape = None
        meta = node.meta.get("tensor_meta")
        if meta is not None and hasattr(meta, "shape"):
            shape = list(meta.shape)
        nodes.append(
            {
                "id": node_ids[node.name],
                "name": str(node.target),
                "op": detail if op == "call_module" else op,
                "params": params,
                "shape": shape,
            }
        )
        edges.extend(
            {"from": node_ids[arg.name], "to": node_ids[node.name]}
            for arg in node.all_input_nodes
        )
    return {"kind": "fx", "nodes": nodes, "edges": edges}


def _module_tree(model: Any) -> dict[str, Any]:
    nodes = []
    edges = []
    for name, module in model.named_modules():
        node_id = name or "model"
        params = sum(p.numel() for p in module.parameters(recurse=False))
        nodes.append(
            {
                "id": node_id,
                "name": node_id,
                "op": type(module).__name__,
                "params": params,
                "shape": None,
            }
        )
        if name:
            parent = name.rsplit(".", 1)[0] if "." in name else "model"
            edges.append({"from": parent, "to": node_id})
    return {"kind": "modules", "nodes": nodes, "edges": edges}


def _emit_stats(model: Any) -> None:
    try:
        stats = {}
        for name, module in model.named_modules():
            node_id = name or "model"
            w_norm = 0.0
            g_norm = 0.0
            for p in module.parameters(recurse=False):
                w_norm += float(p.detach().norm())
                if p.grad is not None:
                    g_norm += float(p.grad.detach().norm())
            if w_norm or g_norm:
                stats[node_id] = {"w_norm": w_norm, "g_norm": g_norm}
        if stats:
            _emit({"type": "model_stats", "stats": stats, "ts": time.time()})
    except Exception:
        pass  # stats must never break a training loop


def callback(name=None, log_every=1):
    """A Hugging Face `TrainerCallback` that mirrors every logged metric here.

    This is what makes "local metrics are authoritative" true rather than a
    slogan: it is installed on every generated recipe regardless of
    `report_to`, so the chart pane works offline, works with `report_to=["none"]`,
    and keeps working when a tracker's API key expires mid-run.

    `transformers` is imported **inside** this function. The helper package has
    zero dependencies by design — importing a trainer at module scope would make
    `import horrible_train` fail in a plain script or a gym rollout, which is
    most of what this package is used for.
    """
    from transformers import TrainerCallback  # noqa: PLC0415 — see the docstring

    class HorribleCallback(TrainerCallback):
        def __init__(self):
            self.started = False

        def on_train_begin(self, args, state, control, **kwargs):
            if not self.started:
                run(name or "train")
                self.started = True

        def on_log(self, args, state, control, logs=None, **kwargs):
            if not logs:
                return
            step = int(getattr(state, "global_step", 0) or 0)
            if log_every > 1 and step % log_every:
                return
            # Only scalars: `logs` also carries strings and the odd nested dict,
            # and a chart of a string is a crash rather than an empty series.
            values = {}
            for key, value in logs.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                values[key] = float(value)
            if values:
                log(step=step, **values)

    return HorribleCallback()
