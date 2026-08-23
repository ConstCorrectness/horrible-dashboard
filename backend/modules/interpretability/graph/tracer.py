"""The second importer: an `nn.Module` that exists, traced back into a design.

The GGUF importer reads *metadata* — someone's description of a model. This one reads
the model. `torch.fx.symbolic_trace` records the operations a `forward` actually
performs, in order, with real propagated shapes, and that trace becomes a graph you
can edit.

It runs as a subprocess in a training project's venv, for the same reason `probe.py`
does and with the same self-contained script: the backend has no torch, and a
project's venv is not guaranteed to carry our helper package either.

**The honest part, which is most of the design.** A trace is in torch's vocabulary,
not ours — twenty-one node types cannot describe every module anyone might write. So:

- A module we recognise (`nn.Linear`, `nn.Embedding`, `nn.LayerNorm`, …) becomes the
  node that generates it.
- Everything else becomes a `custom.module` whose body **raises
  `NotImplementedError`**, naming the class it stands for. That is deliberate and it
  is the whole ethic of this module in one decision: the alternative is a stub that
  returns its input unchanged, which would compile, run, train, and be a different
  model than the one you traced — wrong in a way nothing would ever tell you. A design
  that refuses to run until you fill in the blanks is worth far more than one that
  quietly computes the wrong thing.
- The result reports how many nodes were mapped and how many are placeholders, so
  "this is your model" is never claimed for something that is a sketch of it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from backend.modules.interpretability.graph.models import (
    DesignGraph,
    GraphEdge,
    GraphNode,
)
from backend.modules.training.envs import python_path, venv_exists
from backend.modules.training.models import ProjectModel

logger = logging.getLogger(__name__)

TIMEOUT_S = 180

#: torch module class name → the node type that generates it, and the params to
#: carry across. Kept small and literal on purpose: a mapping that guesses is how a
#: traced `GEGLU` silently becomes a `SwiGLU`.
KNOWN: dict[str, str] = {
    "Linear": "ffn.linear",
    "Embedding": "embed.token",
    "LayerNorm": "norm.layer",
    "RMSNorm": "norm.rms",
    "Dropout": "op.dropout",
}


class TraceResult(BaseModel):
    """A design traced out of a running module, and how much of it we understood."""

    graph: DesignGraph | None = None
    status: str = "unavailable"  # traced | failed | unavailable
    message: str = ""
    traceback: str = ""
    #: Nodes that became a real node type.
    mapped: int = 0
    #: Nodes that became a `custom.module` placeholder, by class name. Named,
    #: because an opaque import nobody is told about is indistinguishable from a
    #: wrong one — the same rule the source parser follows.
    placeholders: list[str] = Field(default_factory=list)
    torchVersion: str = ""


_SCRIPT = r"""
import json, sys, traceback, importlib, time

def main():
    target, batch, seq = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    try:
        import torch, torch.fx
    except Exception as exc:
        print(json.dumps({"status": "unavailable", "message": f"torch is not installed in this project's venv ({exc})"}))
        return

    out = {"status": "failed", "torch": torch.__version__}
    try:
        module_name, _, class_name = target.rpartition(".")
        if not module_name:
            raise ValueError("give the module as package.module.ClassName")
        cls = getattr(importlib.import_module(module_name), class_name)
        model = cls()
    except Exception:
        out["traceback"] = traceback.format_exc()
        out["message"] = "could not import and construct that class"
        print(json.dumps(out)); return

    try:
        traced = torch.fx.symbolic_trace(model)
    except Exception:
        out["traceback"] = traceback.format_exc()
        out["message"] = "symbolic_trace refused this module (data-dependent control flow usually)"
        print(json.dumps(out)); return

    # Shapes are decoration; the topology is the thing. A failed ShapeProp must not
    # lose the trace we already have.
    try:
        from torch.fx.passes.shape_prop import ShapeProp
        ShapeProp(traced).propagate(torch.randint(0, 100, (batch, seq)))
    except Exception:
        pass

    modules = dict(model.named_modules())
    nodes, edges = [], []
    for node in traced.graph.nodes:
        kind = node.op
        cls_name = ""
        params = 0
        if kind == "call_module" and str(node.target) in modules:
            sub = modules[str(node.target)]
            cls_name = type(sub).__name__
            params = int(sum(p.numel() for p in sub.parameters()))
        meta = node.meta.get("tensor_meta")
        nodes.append({
            "id": node.name,
            "op": kind,
            "target": str(node.target),
            "cls": cls_name,
            "params": params,
            "shape": [int(d) for d in meta.shape] if meta is not None and hasattr(meta, "shape") else None,
        })
        for arg in node.all_input_nodes:
            edges.append({"from": arg.name, "to": node.name})

    print(json.dumps({"status": "traced", "torch": torch.__version__, "nodes": nodes, "edges": edges}))

main()
"""


def trace(project: ProjectModel | None, target: str) -> TraceResult:
    """Trace `package.module.ClassName` in a project's venv. Never raises."""
    if project is None:
        return TraceResult(
            message="Pick a training project — tracing needs a venv with torch, and the backend has none."
        )
    if not venv_exists(project):
        return TraceResult(
            message=f"{project.name} has no environment yet — create it in the training pane first."
        )
    if not target.strip():
        return TraceResult(
            message="Name the module to trace, as package.module.ClassName."
        )

    try:
        out = subprocess.run(
            [str(python_path(project)), "-c", _SCRIPT, target.strip(), "2", "8"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=project.root,
        )
    except subprocess.TimeoutExpired:
        return TraceResult(
            message=f"Tracing did not finish within {TIMEOUT_S}s and was stopped."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TraceResult(message=f"Could not run this project's python: {exc}")

    try:
        payload = json.loads((out.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return TraceResult(
            message="The tracer printed nothing readable, which usually means the venv itself is broken.",
            traceback=(out.stderr or out.stdout or "").strip()[-400:],
        )

    status = str(payload.get("status", "failed"))
    if status != "traced":
        return TraceResult(
            status="failed" if status == "failed" else "unavailable",
            message=str(payload.get("message", "the module could not be traced")),
            traceback=str(payload.get("traceback", "")),
            torchVersion=str(payload.get("torch", "")),
        )

    graph, mapped, placeholders = build(
        payload.get("nodes") or [], payload.get("edges") or [], target
    )
    return TraceResult(
        graph=graph,
        status="traced",
        mapped=mapped,
        placeholders=placeholders,
        torchVersion=str(payload.get("torch", "")),
        message=(
            f"Traced {mapped + len(placeholders)} operations. "
            + (
                f"{len(placeholders)} could not be mapped onto a node type and are placeholders "
                "that raise until you fill them in."
                if placeholders
                else "Every one mapped onto a node type."
            )
        ),
    )


def build(
    raw_nodes: list[dict[str, Any]], raw_edges: list[dict[str, Any]], target: str
) -> tuple[DesignGraph, int, list[str]]:
    """Turn a trace into a design. Pure, so it is testable without torch."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    placeholders: list[str] = []
    mapped = 0
    kept: set[str] = set()

    for raw in raw_nodes:
        nid = str(raw.get("id"))
        op = str(raw.get("op"))
        if op == "placeholder":
            nodes.append(GraphNode(id=nid, type="io.input"))
            kept.add(nid)
            continue
        if op == "output":
            nodes.append(GraphNode(id=nid, type="io.output"))
            kept.add(nid)
            continue

        cls_name = str(raw.get("cls") or raw.get("target") or "Op")
        node_type = KNOWN.get(cls_name)
        if node_type:
            mapped += 1
            nodes.append(
                GraphNode(id=nid, type=node_type, params=_params_for(node_type, raw))
            )
        else:
            placeholders.append(cls_name)
            nodes.append(
                GraphNode(
                    id=nid,
                    type="custom.module",
                    params={
                        "class_name": _safe_class(cls_name),
                        "code": _stub(cls_name),
                        "args": "",
                    },
                )
            )
        kept.add(nid)

    for raw in raw_edges:
        source, dest = str(raw.get("from")), str(raw.get("to"))
        if source in kept and dest in kept:
            edges.append(
                GraphEdge(
                    id=f"{source}->{dest}",
                    source=source,
                    target=dest,
                    targetHandle="in",
                )
            )

    graph = DesignGraph(
        name=target.rpartition(".")[2] or "Traced",
        config={},
        nodes=nodes,
        edges=edges,
    )
    return graph, mapped, sorted(set(placeholders))


def _params_for(node_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Only what the trace actually stated. A shape it did not propagate stays
    unset rather than being invented — the same rule the GGUF importer follows."""
    shape = raw.get("shape") or []
    width = int(shape[-1]) if shape else 0
    if node_type == "ffn.linear" and width:
        return {"out_features": width}
    return {}


def _safe_class(raw: str) -> str:
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == "_") or "Traced"
    return cleaned if not cleaned[0].isdigit() else f"M{cleaned}"


def _stub(cls_name: str) -> str:
    """A placeholder that fails loudly rather than one that lies quietly.

    A stub returning its input unchanged would compile, run, train, and be a
    different model than the one traced — wrong in a way nothing would ever report.
    """
    safe = _safe_class(cls_name)
    return (
        f"class {safe}(nn.Module):\n"
        f'    """Placeholder for the traced `{cls_name}`, which the designer has no node for.\n'
        "\n"
        "    Replace this with the real implementation. It raises rather than passing\n"
        "    its input through, because a pass-through would train fine and be a\n"
        "    different model than the one that was traced.\n"
        '    """\n'
        "\n"
        "    def forward(self, x):\n"
        f'        raise NotImplementedError("{safe} was traced but not implemented")\n'
    )
