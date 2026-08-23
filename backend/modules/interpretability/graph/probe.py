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
from backend.modules.interpretability.graph.models import DesignGraph
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

    project: str = ""
    torchVersion: str = ""
    durationMs: int = 0


_SCRIPT = r"""
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
    print(json.dumps({
        "status": "ran",
        "torch": out.get("torch", ""),
        "shape": [int(d) for d in shape],
        "params": int(sum(p.numel() for p in model.parameters())),
        "ms": int((time.time() - started) * 1000),
    }))

main()
"""


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

    return _read_output(out, project, estimated)


def _read_output(
    out: subprocess.CompletedProcess[str],
    project: ProjectModel,
    estimated: int | None,
) -> ProbeResult:
    """Turn the subprocess's last line into a result, or say why we cannot."""
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
    return ProbeResult(
        status="ran",
        outputShape=[int(d) for d in payload.get("shape", [])],
        totalParams=measured,
        estimatedParams=estimated,
        # A disagreement is a finding, not a rounding error: the estimate is what the
        # cost overlay shows on every keystroke, so if it is wrong the pane has been
        # lying quietly. Surfaced rather than reconciled.
        agrees=None if estimated is None else measured == estimated,
        project=project.id,
        torchVersion=str(payload.get("torch", "")),
        durationMs=int(payload.get("ms", 0)),
    )
