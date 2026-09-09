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


class PorcelainEntry:
    """One line of `git status --porcelain=v2`, parsed but not yet collapsed.

    The two status characters are kept **apart** here, which is the whole reason
    this exists: `GitStatus` collapses them into one category for the file tree's
    decorations, and that collapse is lossy in exactly the way source control
    cares about — it cannot tell a staged change from an unstaged one.
    """

    __slots__ = ("path", "index", "worktree", "orig_path")

    def __init__(
        self, path: str, index: str, worktree: str, orig_path: str | None = None
    ):
        self.path = path
        self.index = index
        self.worktree = worktree
        self.orig_path = orig_path

    @property
    def xy(self) -> str:
        return f"{self.index}{self.worktree}"

    @property
    def status(self) -> str:
        return _status_of(self.xy)


class Porcelain:
    """A parsed `git status --porcelain=v2 --branch` run."""

    __slots__ = ("branch", "ahead", "behind", "entries")

    def __init__(self) -> None:
        self.branch: str | None = None
        self.ahead = 0
        self.behind = 0
        self.entries: list[PorcelainEntry] = []


def parse_porcelain_v2(out: str, repo_root: Path) -> Porcelain:
    """Parse porcelain v2 into absolute paths and split status characters.

    One parser, two consumers (`git_status` here and the source-control view in
    the git module) so the two can never come to disagree about what a `2 ` line
    means — which is the kind of drift that shows a rename in one pane and a
    delete-plus-add in another.
    """
    parsed = Porcelain()
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            parsed.branch = None if head == "(detached)" else head
        elif line.startswith("# branch.ab "):
            # `+1 -2` — ahead of and behind the upstream.
            for token in line[len("# branch.ab ") :].split():
                if token.startswith("+"):
                    parsed.ahead = int(token[1:] or 0)
                elif token.startswith("-"):
                    parsed.behind = int(token[1:] or 0)
        elif line.startswith("1 "):  # ordinary change: 8 fields then path
            parts = line.split(" ", 8)
            parsed.entries.append(
                PorcelainEntry(_abs(repo_root, parts[8]), parts[1][0], parts[1][1])
            )
        elif line.startswith("2 "):  # rename/copy: extra score field, path<TAB>orig
            parts = line.split(" ", 9)
            path, _, orig = parts[9].partition("	")
            parsed.entries.append(
                PorcelainEntry(
                    _abs(repo_root, path),
                    parts[1][0],
                    parts[1][1],
                    _abs(repo_root, orig) if orig else None,
                )
            )
        elif line.startswith("u "):  # unmerged: 9 fields then path
            parts = line.split(" ", 10)
            parsed.entries.append(
                PorcelainEntry(_abs(repo_root, parts[10]), parts[1][0], parts[1][1])
            )
        elif line.startswith("? "):  # untracked
            parsed.entries.append(PorcelainEntry(_abs(repo_root, line[2:]), "?", "?"))
    return parsed


def read_porcelain(root: Path) -> tuple[Path, Porcelain] | None:
    """Run status in `root` and parse it. None when `root` is not in a repo."""
    top = _run_git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    repo_root = Path(top.strip())
    out = _run_git(root, "status", "--porcelain=v2", "--branch") or ""
    return repo_root, parse_porcelain_v2(out, repo_root)


def git_status(root: Path) -> GitStatus:
    read = read_porcelain(root)
    if read is None:
        return GitStatus(is_repo=False, root=str(root))
    _, parsed = read
    entries = [
        GitEntry(
            path=e.path,
            status="untracked" if e.xy == "??" else e.status,
        )
        for e in parsed.entries
    ]
    return GitStatus(
        is_repo=True, root=str(root), branch=parsed.branch, entries=entries
    )
