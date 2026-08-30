"""Which shells this machine can actually run, and how to launch each one.

**Why this exists.** `default_shell()` answers one question — what to spawn when
nobody said — and on Windows it answers `powershell.exe` unconditionally. That is a
reasonable default and a terrible ceiling: the box very often has Git Bash sitting in
`Program Files`, and until now there was no way to ask for it. This module enumerates
what is present so the pane can offer a choice, without changing what the default is.

**The trap, and the reason detection is by absolute path only.** On Windows,
`shutil.which("bash")` resolves to `C:\\Windows\\System32\\bash.exe` — the **WSL
launcher shim**, not Git Bash. Labelling that "Bash" would hand the user a shell whose
paths are `/mnt/c/...`, where every `cwd` the rest of the app passes is wrong and every
file path it prints is unusable by the file explorer next to it. Nothing errors; the
shell just quietly means something else. WSL is a fine thing to offer, but only under
its own name, so the user knows which filesystem they are standing in.

**Three states, not two** — the `hardware` module's rule. A shell we looked for and did
not find is a different fact from one we could not check (`wsl.exe` present but hanging,
a probe we are not allowed to run), and a picker that renders the second as the first is
lying about the machine. Hence `ShellInfo.note`.

**Sync on purpose**, same as the hardware probe: these are short-lived subprocesses, and
asyncio subprocess spawning is broken under `uvicorn --reload` on Windows
(SelectorEventLoop). Callers reach this through `asyncio.to_thread`.

See docs/modules/terminal.mdx.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.modules.terminal.pty import default_shell

logger = logging.getLogger(__name__)

#: `wsl.exe -l -q` on a machine with no distros installed can sit for a while.
_TIMEOUT = 6.0


@dataclass(frozen=True)
class ShellInfo:
    """One launchable shell.

    `id` is what crosses the wire. It is deliberately **not** a path: see
    `resolve()`.
    """

    id: str
    label: str
    #: Broad family, for the UI's icon/grouping. Not used to pick behaviour.
    kind: str
    argv: list[str] = field(default_factory=list)
    #: Why this entry is worth knowing about, or what we could not determine.
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            # The path is shown so the user can tell two bashes apart; it is
            # display-only and is never accepted back.
            "path": self.argv[0] if self.argv else "",
            "note": self.note,
        }


def _git_bash_candidates() -> list[Path]:
    """Absolute paths Git for Windows installs `bash.exe` at, most likely first.

    `bin\\bash.exe` rather than `usr\\bin\\bash.exe`: the former is the launcher that
    puts the MSYS runtime on its own PATH, the latter needs those DLLs already there
    and fails to start with no useful message when they are not.
    """
    roots: list[str] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value:
            roots.append(value)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(str(Path(local) / "Programs"))

    candidates = [Path(root) / "Git" / "bin" / "bash.exe" for root in roots]

    # Last resort: a Git on PATH tells us the install root even when it is somewhere
    # unusual. `git.exe` lives in `<root>\cmd` or `<root>\bin`, so the sibling `bin`
    # is one level up either way.
    git = shutil.which("git")
    if git:
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _wsl_distros() -> tuple[list[str], str | None]:
    """Installed WSL distro names, and a note when we could not find out.

    Returns `([], None)` for "asked, none installed" and `([], "<why>")` for "could
    not ask" — the distinction the whole module exists to preserve.
    """
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if wsl is None:
        return [], None
    try:
        res = subprocess.run(
            [wsl, "-l", "-q"],
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("wsl probe failed: %s", exc)
        return [], f"WSL is installed but did not answer ({exc.__class__.__name__})."
    if res.returncode != 0:
        return [], "WSL is installed but listing its distributions failed."
    # `wsl -l -q` writes UTF-16LE, and decoding it as UTF-8 yields names full of NULs
    # that look like garbage rather than failing.
    text = res.stdout.decode("utf-16-le", errors="replace")
    names = [line.strip().strip("\x00") for line in text.splitlines()]
    return [n for n in names if n], None


def _windows_shells() -> list[ShellInfo]:
    found: list[ShellInfo] = []

    for path in _git_bash_candidates():
        if path.is_file():
            found.append(
                ShellInfo(
                    id="git-bash",
                    label="Git Bash",
                    kind="bash",
                    # `-i` so it is an interactive shell; `-l` so the user's
                    # `.bash_profile` is sourced and the prompt/aliases they expect
                    # are there.
                    argv=[str(path), "-i", "-l"],
                    note="Real bash on the Windows filesystem — same paths as the rest of the app.",
                )
            )
            break

    msys = Path("C:/msys64/usr/bin/bash.exe")
    if msys.is_file():
        found.append(
            ShellInfo(
                id="msys2-bash",
                label="MSYS2 Bash",
                kind="bash",
                argv=[str(msys), "-i", "-l"],
            )
        )

    for shell_id, label, exe in (
        ("pwsh", "PowerShell 7", "pwsh.exe"),
        ("powershell", "Windows PowerShell", "powershell.exe"),
        ("cmd", "Command Prompt", "cmd.exe"),
    ):
        # These three are genuinely PATH-resolved: unlike `bash`, there is no
        # differently-behaving impostor sitting in System32 under the same name.
        resolved = shutil.which(exe)
        if resolved:
            found.append(
                ShellInfo(id=shell_id, label=label, kind=shell_id, argv=[resolved])
            )

    distros, wsl_note = _wsl_distros()
    for distro in distros:
        found.append(
            ShellInfo(
                id=f"wsl:{distro}",
                label=f"WSL — {distro}",
                kind="wsl",
                argv=["wsl.exe", "-d", distro],
                note="Linux filesystem: Windows paths appear under /mnt/c.",
            )
        )
    if wsl_note:
        # An entry with no argv is not launchable and `resolve` will not accept it.
        # It exists so the picker can say "we could not check" rather than showing
        # nothing, which reads as "you have no WSL".
        found.append(
            ShellInfo(id="wsl:unknown", label="WSL", kind="wsl", note=wsl_note)
        )

    return found


#: POSIX shells worth offering, beyond whatever `$SHELL` names.
_POSIX_CANDIDATES = (
    ("bash", "Bash", "bash"),
    ("zsh", "Zsh", "zsh"),
    ("fish", "Fish", "fish"),
    ("sh", "sh", "sh"),
)


def _posix_shells() -> list[ShellInfo]:
    found: list[ShellInfo] = []
    seen: set[str] = set()

    login = os.environ.get("SHELL")
    resolved_login = None
    if login:
        resolved_login = login if os.path.isfile(login) else shutil.which(login)
    if resolved_login:
        seen.add(Path(resolved_login).name)
        found.append(
            ShellInfo(
                id="login",
                label=f"Login shell ({Path(resolved_login).name})",
                kind=Path(resolved_login).name,
                argv=[resolved_login],
                note="Your $SHELL.",
            )
        )

    for shell_id, label, exe in _POSIX_CANDIDATES:
        if exe in seen:
            continue
        resolved = shutil.which(exe)
        if resolved:
            seen.add(exe)
            found.append(ShellInfo(id=shell_id, label=label, kind=exe, argv=[resolved]))

    return found


def discover() -> list[ShellInfo]:
    """Every launchable shell on this machine, best first. Uncached."""
    return _windows_shells() if sys.platform == "win32" else _posix_shells()


_cache: list[ShellInfo] | None = None
_lock = threading.Lock()


def discover_shells(refresh: bool = False) -> list[ShellInfo]:
    """Cached `discover()`. Installing a shell mid-session needs `refresh=True`."""
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = discover()
        return _cache


def reset_cache() -> None:
    """Test seam, and the hook behind the route's `?refresh=1`."""
    global _cache
    with _lock:
        _cache = None


def default_shell_id() -> str | None:
    """The id matching what `default_shell()` would spawn, if one of ours matches.

    Used only to mark the default in the picker. `resolve(None)` does not depend on
    it — the fallback is `default_shell()` itself, so an unmatched default still
    launches the right thing.
    """
    target = Path(default_shell()).name.lower()
    for shell in discover_shells():
        if shell.argv and Path(shell.argv[0]).name.lower() == target:
            return shell.id
    return None


def resolve(shell_id: str | None) -> list[str]:
    """Argv for a shell **id**, falling back to the platform default.

    This is the security boundary for the terminal channel, and it is why the wire
    carries an id rather than a path. Anything that can open the websocket can send a
    `start` frame; if that frame could name an executable, the terminal would be an
    arbitrary-exec route with a PTY attached. An id is matched against the discovered
    set and nothing else — an unknown one (or a path-shaped one) yields the default,
    which `manager` then reports back so the pane can say it did not get what it asked
    for.
    """
    if shell_id:
        for shell in discover_shells():
            if shell.id == shell_id and shell.argv:
                return list(shell.argv)
    return [default_shell()]


def is_known(shell_id: str | None) -> bool:
    """Did `resolve` honour this id, or fall back? The manager reports the difference."""
    if not shell_id:
        return True
    return any(s.id == shell_id and s.argv for s in discover_shells())
