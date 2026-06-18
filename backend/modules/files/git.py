"""Working-tree status for a workspace root, via `git status --porcelain=v2`.

Parses git's stable machine format into absolute path → collapsed status, so the
file tree can paint VS Code-style decorations and the agent can read "what changed".
Returns `is_repo=False` (not an error) when the root isn't inside a repo, so a
non-git workspace simply shows no decorations. See docs/modules/file-explorer.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.modules.files.models import GitEntry, GitStatus

_GIT_TIMEOUT_S = 10


def _run_git(root: Path, *args: str) -> str | None:
    """Run git in `root`; return stdout, or None if git is missing/errored."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _status_of(xy: str) -> str:
    """Collapse git's two-char staged/unstaged code into one category. Order
    matters — a conflict/delete/add is more salient than a plain modification."""
    chars = set(xy) - {".", " "}
    if "U" in chars:
        return "conflict"
    if "D" in chars:
        return "deleted"
    if "A" in chars:
        return "added"
    if "R" in chars or "C" in chars:
        return "renamed"
    return "modified"


def _abs(repo_root: Path, rel: str) -> str:
    """Absolute, OS-native path for a repo-relative git path (forward-slashed)."""
    return str(repo_root / rel)


def git_status(root: Path) -> GitStatus:
    top = _run_git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return GitStatus(is_repo=False, root=str(root))
    repo_root = Path(top.strip())
    out = _run_git(root, "status", "--porcelain=v2", "--branch") or ""

    branch: str | None = None
    entries: list[GitEntry] = []
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            branch = None if head == "(detached)" else head
        elif line.startswith("1 "):  # ordinary change: 8 fields then path
            parts = line.split(" ", 8)
            entries.append(
                GitEntry(path=_abs(repo_root, parts[8]), status=_status_of(parts[1]))
            )
        elif line.startswith("2 "):  # rename/copy: extra score field, path<TAB>orig
            parts = line.split(" ", 9)
            entries.append(
                GitEntry(
                    path=_abs(repo_root, parts[9].split("\t", 1)[0]), status="renamed"
                )
            )
        elif line.startswith("u "):  # unmerged: 9 fields then path
            parts = line.split(" ", 10)
            entries.append(GitEntry(path=_abs(repo_root, parts[10]), status="conflict"))
        elif line.startswith("? "):  # untracked
            entries.append(GitEntry(path=_abs(repo_root, line[2:]), status="untracked"))

    return GitStatus(is_repo=True, root=str(root), branch=branch, entries=entries)
