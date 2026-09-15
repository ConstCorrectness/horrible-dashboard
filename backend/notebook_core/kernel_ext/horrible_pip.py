"""IPython extension loaded into every notebook kernel: `%pip` and `!pip` through uv.

This file runs **inside the kernel process**, not the backend — `KernelSession`
prepends this directory to the kernel's `PYTHONPATH` and names the extension in the
kernelspec argv. So it may import only the stdlib and IPython; `backend.*` is not
importable from here.

Why it exists: every kernel interpreter this app manages is a **uv venv, and a uv venv
has no pip**. IPython's own `%pip` runs `sys.executable -m pip` ("No module named pip"),
and `!pip` shells out to whichever `pip` is first on `PATH` — the kernel inherits the
backend's environment, so that is the *backend's* venv or a system pip, which installs
into a different interpreter and reports success. Both now run
`uv pip <cmd> --python <sys.executable>`: the interpreter the cell is actually running on.

`!pip …` is rewritten to `%pip …` by an input transformer rather than handled
separately, so there is exactly one code path. Any other `!` command stays a shell
command.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

#: `!pip …`, `!pip3 …`, `!python -m pip …`, `!uv pip …` — the spellings people paste
#: from READMEs. The lookahead keeps `!pipx` a shell command.
_BANG_PIP = re.compile(
    r"^(?P<indent>\s*)!\s*(?:pip3?|python3?\s+-m\s+pip|uv\s+pip)(?=\s|$)(?P<rest>.*)$"
)

#: Subcommands passed through. Anything else (`config`, `cache`, `download`) either has
#: no uv equivalent or means something different there, so it is refused, not guessed.
SUBCOMMANDS = frozenset({"install", "uninstall", "list", "show", "freeze"})
_MUTATING = frozenset({"install", "uninstall"})

#: Options whose value is the next token — skipped when reading package names.
_OPTS_WITH_VALUE = frozenset(
    {
        "-r",
        "--requirement",
        "-c",
        "--constraint",
        "-e",
        "--editable",
        "-i",
        "--index-url",
        "--extra-index-url",
        "-f",
        "--find-links",
        "-p",
        "--python",
    }
)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def rewrite_lines(lines: list[str]) -> list[str]:
    """Input transformer (`input_transformers_cleanup`): `!pip …` → `%pip …`."""
    out: list[str] = []
    for line in lines:
        body = line[:-1] if line.endswith("\n") else line
        m = _BANG_PIP.match(body)
        if m is None:
            out.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        out.append(f"{m['indent']}%pip{m['rest']}{newline}")
    return out


def split_args(line: str) -> list[str]:
    """Shell-split a magic's argument line. Non-POSIX on Windows so a backslash in
    `-r C:\\path\\req.txt` stays a path separator rather than an escape."""
    if os.name != "nt":
        return shlex.split(line)
    parts = shlex.split(line, posix=False)
    return [
        p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts
    ]


def resolve_uv() -> str | None:
    """uv as resolved by the backend (`HORRIBLE_UV`), else whatever is on PATH."""
    configured = os.environ.get("HORRIBLE_UV", "")
    if configured and os.path.isfile(configured):
        return configured
    return shutil.which("uv")


def build_command(args: list[str], *, uv: str | None, python: str) -> list[str]:
    """The argv for one `%pip` invocation. Raises ValueError on an unsupported one."""
    if not args or args[0] not in SUBCOMMANDS:
        allowed = ", ".join(sorted(SUBCOMMANDS))
        raise ValueError(f"%pip supports {allowed}")
    sub, rest = args[0], args[1:]
    if uv is None:
        return [python, "-m", "pip", sub, *rest]
    translated: list[str] = []
    for arg in rest:
        if sub == "uninstall" and arg in ("-y", "--yes"):
            continue  # uv never prompts, and rejects the flag
        translated.append("--no-cache" if arg == "--no-cache-dir" else arg)
    return [uv, "pip", sub, "--python", python, *translated]


def canonical(name: str) -> str:
    """PEP 503 normalization, so `Scikit_Learn` and `scikit-learn` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requested_names(args: list[str]) -> set[str]:
    """Distribution names named on an install/uninstall line (canonical). Paths, URLs
    and option values are skipped — they name no distribution we could look up."""
    names: set[str] = set()
    skip_next = False
    for arg in args[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            skip_next = arg in _OPTS_WITH_VALUE
            continue
        if "/" in arg or "\\" in arg or arg.endswith((".whl", ".tar.gz", ".zip")):
            continue
        m = _NAME.match(arg)
        if m:
            names.add(canonical(m.group(0)))
    return names


def stale_modules(names: set[str]) -> list[str]:
    """Already-imported top-level modules belonging to `names`. Call **before** the
    install/uninstall runs — afterwards an uninstalled distribution has no metadata."""
    if not names:
        return []
    hits: set[str] = set()
    try:
        mapping = importlib.metadata.packages_distributions()
    except Exception:  # a broken dist-info must not break the install itself
        mapping = {}
    for module, dists in mapping.items():
        if module in sys.modules and any(canonical(d) in names for d in dists):
            hits.add(module)
    for name in names:  # a dist with no top-level mapping: fall back to its own name
        guess = name.replace("-", "_")
        if guess in sys.modules:
            hits.add(guess)
    return sorted(hits)


def _run(cmd: list[str]) -> int:
    """Run to completion, streaming merged output into the cell. Blocking `Popen` —
    the codebase's Windows-safe spawn pattern."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
    return proc.wait()


def pip_magic(line: str) -> None:
    """Install into this kernel's interpreter with uv: `%pip install regex`.

    `!pip install regex` is rewritten to this. Supports install, uninstall, list, show
    and freeze. A newly installed package imports without a restart; upgrading one that
    is already imported needs a kernel restart, and you are told which.
    """
    from IPython.core.error import UsageError

    args = split_args(line)
    uv = resolve_uv()
    try:
        cmd = build_command(args, uv=uv, python=sys.executable)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    if uv is None:
        print("uv not found — falling back to `python -m pip`", flush=True)

    mutating = args[0] in _MUTATING
    stale = stale_modules(requested_names(args)) if mutating else []
    code = _run(cmd)
    if code != 0:
        program = os.path.splitext(os.path.basename(cmd[0]))[0].lower()
        raise UsageError(f"{program} {' '.join(cmd[1:3])} exited with {code}")
    if mutating:
        importlib.invalidate_caches()
        if stale:
            print(
                f"Restart the kernel to pick up the change to: {', '.join(stale)}",
                flush=True,
            )


def uv_magic(line: str) -> None:
    """`%uv pip install regex` — the same as `%pip install regex`."""
    from IPython.core.error import UsageError

    head, _, rest = line.strip().partition(" ")
    if head != "pip":
        raise UsageError("%uv supports only `%uv pip …`")
    pip_magic(rest)


def load_ipython_extension(ip: Any) -> None:
    ip.register_magic_function(pip_magic, magic_kind="line", magic_name="pip")
    ip.register_magic_function(uv_magic, magic_kind="line", magic_name="uv")
    if rewrite_lines not in ip.input_transformers_cleanup:
        ip.input_transformers_cleanup.append(rewrite_lines)
