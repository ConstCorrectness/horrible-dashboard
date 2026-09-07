"""Running a model's code to see whether it works — the HumanEval-shaped grade.

The existing metrics are `exact_match` and `contains`. Neither can score code:
two correct solutions to the same problem share almost no characters, and a
solution that differs from the reference by a variable name scores zero. That is
why HumanEval and MBPP were absent from the presets — not because the datasets are
hard to fetch, but because there was no scorer.

**Gated, and the gate is the honest part.** `HORRIBLE_ENABLE_EVAL_CODE_EXEC=1` is
required, and the docstring says what the games module's already says: subprocess
isolation is **not** container-grade. A separate process with a timeout and a
scrubbed environment stops a runaway loop and an accidental `print(os.environ)`; it
does not stop a determined program. This runs code a language model wrote about a
programming puzzle, on the user's own machine, at the user's explicit request —
that is a reasonable risk to take deliberately and an unreasonable one to take by
default.

**The reference is a test, not an answer.** A code case carries the prompt, the
candidate's completion and the dataset's `test` function; the grade is whether the
tests pass. Nothing is compared as a string anywhere.

Three details that are each a wrong score if missed:

- **Markdown fences are stripped.** Models return ```python blocks; running the
  fence is a `SyntaxError` scored as a wrong answer.
- **The prompt is prepended when the completion does not redefine the function.**
  HumanEval's format is a *completion* — the model is given a signature and returns
  a body. Running the body alone is a `NameError`.
- **A timeout is a fail, not an error.** An infinite loop is a wrong answer to a
  programming problem; treating it as infrastructure trouble hides a real result.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FLAG = "HORRIBLE_ENABLE_EVAL_CODE_EXEC"

#: Per-candidate wall clock. HumanEval solutions run in milliseconds; anything
#: near this is a loop that will not end.
TIMEOUT_S = 10.0

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip() not in ("", "0", "false", "no")


class CodeExecError(RuntimeError):
    """Execution could not be attempted (gate off, no interpreter)."""


def extract_code(reply: str) -> str:
    """The code out of a model reply.

    Fenced blocks win when present — a model that explains its answer and then
    fences it has produced valid code surrounded by prose, and running the prose is
    a SyntaxError graded as a wrong answer.
    """
    blocks = _FENCE.findall(reply or "")
    if blocks:
        return max(blocks, key=len).strip("\n")
    return (reply or "").strip()


def _defines(code: str, name: str) -> bool:
    return (
        bool(name)
        and re.search(rf"^\s*def\s+{re.escape(name)}\s*\(", code, re.M) is not None
    )


def build_program(prompt: str, completion: str, test: str, entry_point: str) -> str:
    """The program that decides the verdict.

    `prompt` is HumanEval's signature-plus-docstring stub. A model may answer with
    the whole function or with just the body; prepending the stub only in the
    second case is what makes both shapes runnable.
    """
    code = extract_code(completion)
    body = (
        code
        if _defines(code, entry_point)
        else f"{prompt}\n{textwrap.indent(code, '    ')}"
    )
    return "\n".join(
        [
            body,
            "",
            test,
            "",
            f"check({entry_point})" if "check(" in test else "",
            "print('__HORRIBLE_PASS__')",
        ]
    )


def run_case(
    *,
    prompt: str,
    completion: str,
    test: str,
    entry_point: str,
    python: str = "",
    timeout: float = TIMEOUT_S,
) -> tuple[bool, str]:
    """`(passed, detail)` — run the candidate against the dataset's tests."""
    if not enabled():
        raise CodeExecError(
            f"code execution is off. Set {ENV_FLAG}=1 to grade code benchmarks. "
            "It runs model-written code in a subprocess on this machine, which is "
            "isolated but NOT container-grade."
        )
    interpreter = python or sys.executable
    program = build_program(prompt, completion, test, entry_point)

    with tempfile.TemporaryDirectory(prefix="horrible-eval-") as folder:
        script = Path(folder) / "candidate.py"
        script.write_text(program, encoding="utf-8")
        try:
            # Blocking `run` on the caller's thread, never asyncio: the same
            # Windows/`--reload` constraint every other subprocess here follows.
            # The environment is scrubbed to the minimum an interpreter needs, so
            # an accidental `print(os.environ)` in a solution leaks nothing.
            out = subprocess.run(
                [interpreter, "-I", str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=folder,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        except subprocess.TimeoutExpired:
            # A fail, not an error: an infinite loop IS a wrong answer to a
            # programming problem, and calling it infrastructure trouble would hide
            # a real result.
            return (
                False,
                f"timed out after {timeout:.0f}s (likely a non-terminating loop)",
            )
        except OSError as exc:
            raise CodeExecError(f"could not run the interpreter: {exc}") from exc

    if "__HORRIBLE_PASS__" in (out.stdout or ""):
        return True, "the dataset's tests passed"
    error = (out.stderr or "").strip().splitlines()
    # The last line of a traceback is the exception; the frames above it are the
    # generated program's line numbers, which mean nothing to the reader.
    detail = error[-1] if error else "the tests did not pass and printed nothing"
    return False, detail[:300]
