"""Tier 2: instantiate the generated module in a real venv and ask torch.

Everything else in this package is *our* arithmetic. `shapes.py` computes what it
believes the shapes are, `spec.py` computes what it believes the parameter counts are,
and both are labelled `estimated` in the pane because that is what they are. This file
is the only thing that can turn an estimate into a measurement, and it does so the only
way available: by running the code.

It runs **in a training project's venv**, never in the backend. The backend has no
torch and must not grow one — heavy dependencies live in per-project uv envs — so the
probe is a `subprocess.run` on the caller's thread (never asyncio: under
`uvicorn --reload` on Windows the loop cannot spawn subprocesses), exactly the shape
`recipes._probe` already uses.

Three states, never two, which is the rule the hardware module exists to enforce:

- **`ran`** — it built, it ran a forward pass, here is the real output shape and the
  real parameter count.
- **`failed`** — it raised, and here is the traceback verbatim. Not summarised: the
  traceback *is* the answer, and paraphrasing it would throw away the line number.
- **`unavailable`** — we could not ask. No project chosen, no venv, no torch. This is
  the state everything else in this codebase gets wrong by collapsing into "fine",
  and reporting a model as validated when nothing validated it is worse than not
  offering the button.

The script it runs is **self-contained**: it does not import `horrible_train`, because
a project's venv is not guaranteed to have it (an evals-owned project carries only the
benchmark's own requirements), and a probe that fails on a technicality about our own
helper package would be reported as the model being broken.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from backend.modules.interpretability.graph import codegen, shapes
from backend.modules.interpretability.graph.models import (
    CodeResult,
    DesignGraph,
    ShapeReport,
)
from backend.modules.training.envs import python_path, venv_exists
from backend.modules.training.models import ProjectModel

logger = logging.getLogger(__name__)

#: Long enough for a cold import of torch on a slow disk, which is most of it.
TIMEOUT_S = 180

#: The dummy batch. Two rows rather than one so a shape bug that only shows up with a
#: batch dimension cannot hide, and short so the whole thing stays under a second once
#: torch is imported.
BATCH, SEQ = 2, 8


class ProbeResult(BaseModel):
    """What actually happened when the module was built and run."""

    status: str = "unavailable"  # ran | failed | unavailable
    #: Why we could not ask, or what went wrong. Always set unless `status == "ran"`.
    message: str = ""
    #: The exception, verbatim, when the module raised. Never summarised.
    traceback: str = ""

    outputShape: list[int] = Field(default_factory=list)
    #: Measured — `sum(p.numel() for p in model.parameters())`.
    totalParams: int | None = None
    #: What `shapes.py` predicted, carried alongside so the two can be compared
    #: rather than one quietly replacing the other.
    estimatedParams: int | None = None
    #: None when there is nothing to compare. False is a real finding: it means the
    #: estimate is wrong, and it is `shapes.py` that needs fixing.
    agrees: bool | None = None

    #: Node id → measured parameters, summed across every copy a ×N stack made.
    #: This is what turns the cost overlay from an estimate into a measurement, one
    #: box at a time.
    nodeParams: dict[str, int] = Field(default_factory=dict)
    #: Node id → whether the measurement agrees with what `shapes.py` predicted for
    #: it. A `False` here is the useful one: it says *which* node's arithmetic is
    #: wrong, which a single total never can.
    nodeAgrees: dict[str, bool] = Field(default_factory=dict)
    #: False when the model had more parameter-holding modules than the probe will
    #: report, so `nodeParams` is a partial picture and must not be read as a full one.
    nodeParamsComplete: bool = True

    project: str = ""
    torchVersion: str = ""
    durationMs: int = 0


#: A model with more param-holding modules than this is one where the per-node
#: overlay stops being the interesting question. Capped so a pathological design
#: cannot return a megabyte of JSON, and reported as incomplete rather than trimmed
#: silently — a per-node count that is quietly missing half its leaves is worse than
#: no per-node count.
MAX_LEAVES = 5000

_SCRIPT = (
    f"MAX_LEAVES = {MAX_LEAVES}\n"
    + r"""
import json, sys, traceback, importlib.util, time

def main():
    path, class_name, vocab, batch, seq = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
    out = {"status": "failed"}
    try:
        import torch
        out["torch"] = torch.__version__
    except Exception as exc:
        print(json.dumps({"status": "unavailable", "message": f"torch is not installed in this project's venv ({exc})"}))
        return

    try:
        spec = importlib.util.spec_from_file_location("horrible_design", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls = getattr(module, class_name)
    except Exception:
        out["traceback"] = traceback.format_exc()
        out["message"] = "the module could not be imported"
        print(json.dumps(out))
        return

    started = time.time()
    try:
        model = cls()
        # Token ids, so an embedding lookup is in range. A float tensor here would
        # fail inside nn.Embedding for a reason that has nothing to do with the design.
        ids = torch.randint(0, max(1, vocab), (batch, seq))
        with torch.no_grad():
            result = model(ids)
    except Exception:
        out["traceback"] = traceback.format_exc()
        out["message"] = "the forward pass raised"
        print(json.dumps(out))
        return

    shape = list(result.shape) if hasattr(result, "shape") else []
    # Every module's *own* parameters, not its subtree's. Reporting subtree totals
    # would double-count the moment anything is nested, and nesting is the normal
    # case here — the mapping back onto nodes happens on the backend, which is the
    # only side that knows which attribute came from which node.
    leaves = {}
    for name, sub in model.named_modules():
        own = sum(p.numel() for p in sub.parameters(recurse=False))
        if own:
            leaves[name] = int(own)
            if len(leaves) >= MAX_LEAVES:
                break
    print(json.dumps({
        "status": "ran",
        "torch": out.get("torch", ""),
        "shape": [int(d) for d in shape],
        "params": int(sum(p.numel() for p in model.parameters())),
        "leaves": leaves,
        "leavesComplete": len(leaves) < MAX_LEAVES,
        "ms": int((time.time() - started) * 1000),
    }))

main()
"""
)


def run(graph: DesignGraph, project: ProjectModel | None) -> ProbeResult:
    """Build the module and run one forward pass. Never raises."""
    estimate = shapes.infer(graph)
    estimated = estimate.totalParams or None

    if project is None:
        return ProbeResult(
            message=(
                "Pick a training project to run this in. The check needs a venv with torch, "
                "and the backend deliberately has neither."
            ),
            estimatedParams=estimated,
        )
    if not venv_exists(project):
        return ProbeResult(
            message=f"{project.name} has no environment yet — create it in the training pane first.",
            project=project.id,
            estimatedParams=estimated,
        )

    generated = codegen.generate(graph)
    if generated.error or not generated.source:
        return ProbeResult(
            message=f"There is no module to run yet: {generated.error or 'the graph generates nothing'}.",
            project=project.id,
            estimatedParams=estimated,
        )

    vocab = graph.config.get("vocab_size", 32000)
    try:
        vocab_size = int(vocab)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        vocab_size = 32000

    with tempfile.TemporaryDirectory(prefix="horrible-design-") as tmp:
        module_path = Path(tmp) / "design_under_test.py"
        module_path.write_text(generated.source, encoding="utf-8")
        try:
            out = subprocess.run(
                [
                    str(python_path(project)),
                    "-c",
                    _SCRIPT,
                    str(module_path),
                    codegen.class_name(graph.name),
                    str(vocab_size),
                    str(BATCH),
                    str(SEQ),
                ],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                cwd=project.root,
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(
                message=f"The forward pass did not finish within {TIMEOUT_S}s and was stopped.",
                project=project.id,
                estimatedParams=estimated,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ProbeResult(
                message=f"Could not run this project's python: {exc}",
                project=project.id,
                estimatedParams=estimated,
            )

    return _read_output(out, project, estimate, generated)


def attribute_params(
    leaves: dict[str, int],
    attrs: dict[str, dict[str, str]],
    attr_classes: dict[str, dict[str, str]],
    root_class: str,
) -> dict[str, int]:
    """Fold measured module paths back onto the nodes that emitted them.

    A runtime path is something like `decoderblocks_1.0.norm_1.weight`'s owner,
    `decoderblocks_1.0.norm_1`. Walking it needs two things codegen hands over: which
    attribute belongs to which node, and which *class* a group attribute holds — the
    `norm_1` under `decoderblocks_1` can only be looked up once you know that slot
    contains a `DecoderBlock`.

    Two rules make the arithmetic come out right:

    - **The deepest mapped node wins.** `attn_1.q_proj` is inside a primitive we
      emitted whole; its weights belong to the attention node, not to some node of
      their own. Once the walk enters a class with no map, everything below is
      charged to the last node it did recognise.
    - **Numeric segments are skipped and their counts accumulate.** A ×N stack is one
      node drawn once, so all N copies of `norm_1` add up to that node's cost —
      exactly what `shapes.py` does when it multiplies an inner node by the count.
    """
    totals: dict[str, int] = {}
    for path, count in leaves.items():
        cls = root_class
        node: str | None = None
        for segment in path.split("."):
            if segment.isdigit():  # a ModuleList index: same class, another copy
                continue
            if not cls:
                break
            found = attrs.get(cls, {}).get(segment)
            if found is None:
                # Inside a primitive or a hand-written class: stop descending and
                # charge the rest to whatever node owns this subtree.
                break
            node = found
            cls = attr_classes.get(cls, {}).get(segment, "")
        if node:
            totals[node] = totals.get(node, 0) + count
    return totals


def _read_output(
    out: subprocess.CompletedProcess[str],
    project: ProjectModel,
    estimate: ShapeReport,
    generated: CodeResult,
) -> ProbeResult:
    """Turn the subprocess's last line into a result, or say why we cannot."""
    estimated = estimate.totalParams or None
    try:
        payload = json.loads((out.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        # The script prints exactly one JSON line; anything else means it died before
        # reaching its own error handling — a broken venv, not a broken model.
        detail = (out.stderr or out.stdout or "").strip()[-400:]
        return ProbeResult(
            message="The probe printed nothing readable, which usually means the venv itself is broken.",
            traceback=detail,
            project=project.id,
            estimatedParams=estimated,
        )

    status = str(payload.get("status", "failed"))
    if status == "unavailable":
        return ProbeResult(
            message=str(payload.get("message", "torch is not available here.")),
            project=project.id,
            estimatedParams=estimated,
        )
    if status != "ran":
        return ProbeResult(
            status="failed",
            message=str(payload.get("message", "the module did not run")),
            traceback=str(payload.get("traceback", "")),
            project=project.id,
            torchVersion=str(payload.get("torch", "")),
            estimatedParams=estimated,
        )

    measured = int(payload.get("params", 0))
    leaves = {str(k): int(v) for k, v in (payload.get("leaves") or {}).items()}
    per_node = attribute_params(
        leaves, generated.attrs, generated.attrClasses, generated.rootClass
    )
    return ProbeResult(
        status="ran",
        outputShape=[int(d) for d in payload.get("shape", [])],
        totalParams=measured,
        estimatedParams=estimated,
        nodeParams=per_node,
        # Only for the nodes we actually predicted a count for. A node with no
        # estimate has nothing to agree or disagree with, and entering `False`
        # would report our own silence as the model's fault.
        nodeAgrees={
            nid: count == estimate.params[nid]
            for nid, count in per_node.items()
            if nid in estimate.params
        },
        nodeParamsComplete=bool(payload.get("leavesComplete", True)),
        # A disagreement is a finding, not a rounding error: the estimate is what the
        # cost overlay shows on every keystroke, so if it is wrong the pane has been
        # lying quietly. Surfaced rather than reconciled.
        agrees=None if estimated is None else measured == estimated,
        project=project.id,
        torchVersion=str(payload.get("torch", "")),
        durationMs=int(payload.get("ms", 0)),
    )
