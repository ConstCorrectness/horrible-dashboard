"""Git operations for the provenance pane: blame (line → commit → session), log,
show (diff), and commit (stamping `X-Horrible-Session` trailers).

Runs `git` as a subprocess like [files/git.py](../files/git.py); the repo root is
derived from a workspace path via `git rev-parse --show-toplevel`. **Provenance** is
the point: a line links to the *agent conversation* that wrote it — `commit` stamps the
active chat session id/title as commit trailers, and `blame` reads them back. See
docs/modules/git.mdx.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.modules.git.models import (
    BlameLine,
    BlameResult,
    CommitInfo,
    CommitResult,
    DiffResult,
    LogResult,
)

_TIMEOUT_S = 15
_SESSION_TRAILER = "X-Horrible-Session"
_TITLE_TRAILER = "X-Horrible-Session-Title"
_ZERO_SHA = "0" * 40
_MAX_LINE_TEXT = 200
_UNIT = "\x1f"  # field separator
_REC = "\x1e"  # record separator


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )


def _out(cwd: Path, *args: str) -> str | None:
    """stdout of a git command, or None if git is missing / the command failed."""
    try:
        proc = _run(cwd, *args)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _repo_root(path: Path) -> Path | None:
    base = path if path.is_dir() else path.parent
    top = _out(base, "rev-parse", "--show-toplevel")
    return Path(top.strip()) if top else None


def _hex(sha: str) -> str:
    """Keep only hex chars — sanitizes a sha before it reaches a git arg."""
    return "".join(c for c in sha if c in "0123456789abcdefABCDEF")


def _session_of(
    root: Path, sha: str, cache: dict[str, tuple[str | None, str | None]]
) -> tuple[str | None, str | None]:
    """(session_id, session_title) from a commit's trailers, cached per sha."""
    if not sha or sha == _ZERO_SHA:
        return None, None
    if sha in cache:
        return cache[sha]
    fmt = (
        f"%(trailers:key={_SESSION_TRAILER},valueonly)"
        f"{_UNIT}%(trailers:key={_TITLE_TRAILER},valueonly)"
    )
    out = (_out(root, "show", "-s", f"--format={fmt}", sha) or "").strip()
    sid, _, title = out.partition(_UNIT)
    res = (sid.strip() or None, title.strip() or None)
    cache[sha] = res
    return res


def blame(path: Path) -> BlameResult:
    """Per-line authorship for a file, each line enriched with the session that wrote
    its commit (the provenance payload)."""
    root = _repo_root(path)
    if root is None:
        return BlameResult(is_repo=False, path=str(path))
    out = _out(root, "blame", "--line-porcelain", "--", str(path))
    if out is None:
        return BlameResult(is_repo=True, path=str(path), root=str(root))

    lines: list[BlameLine] = []
    cache: dict[str, tuple[str | None, str | None]] = {}
    cur: dict[str, str | int] = {}
    for raw in out.splitlines():
        if raw.startswith("\t"):
            # Content line closes the current group.
            sha = str(cur.get("sha", ""))
            sid, title = _session_of(root, sha, cache)
            text = raw[1:]
            lines.append(
                BlameLine(
                    line=int(cur.get("final", len(lines) + 1)),
                    commit=sha[:8],
                    author=str(cur.get("author", "")),
                    summary=str(cur.get("summary", "")),
                    session_id=sid,
                    session_title=title,
                    text=text[:_MAX_LINE_TEXT],
                )
            )
            cur = {}
        elif raw.startswith("author "):
            cur["author"] = raw[len("author ") :]
        elif raw.startswith("summary "):
            cur["summary"] = raw[len("summary ") :]
        else:
            parts = raw.split(" ")
            if (
                len(parts) >= 3
                and len(parts[0]) == 40
                and set(parts[0]) <= set("0123456789abcdef")
            ):
                cur["sha"] = parts[0]
                cur["final"] = int(parts[2]) if parts[2].isdigit() else len(lines) + 1
    return BlameResult(is_repo=True, path=str(path), root=str(root), lines=lines)


def log(path_hint: Path, limit: int = 30) -> LogResult:
    """Recent commits; a session trailer marks a commit agent-authored."""
    root = _repo_root(path_hint)
    if root is None:
        return LogResult(is_repo=False)
    fmt = (
        _UNIT.join(
            [
                "%H",
                "%an",
                "%aI",
                "%s",
                f"%(trailers:key={_SESSION_TRAILER},valueonly)",
                f"%(trailers:key={_TITLE_TRAILER},valueonly)",
            ]
        )
        + _REC
    )
    out = _out(root, "log", f"-n{max(1, limit)}", f"--format={fmt}")
    if out is None:
        return LogResult(is_repo=True)
    commits: list[CommitInfo] = []
    for rec in out.split(_REC):
        rec = rec.strip("\n")
        if not rec:
            continue
        f = rec.split(_UNIT)
        if len(f) < 6:
            continue
        commits.append(
            CommitInfo(
                sha=f[0][:8],
                author=f[1],
                date=f[2],
                summary=f[3],
                session_id=f[4].strip() or None,
                session_title=f[5].strip() or None,
            )
        )
    return LogResult(is_repo=True, commits=commits)


def show(path_hint: Path, sha: str) -> DiffResult:
    """A commit's metadata + unified diff (the review view)."""
    root = _repo_root(path_hint)
    safe = _hex(sha)
    if root is None or not safe:
        return DiffResult(sha=safe[:8], diff="")
    return DiffResult(sha=safe[:8], diff=_out(root, "show", safe) or "")


def commit(
    path_hint: Path, message: str, paths: list[str] | None = None
) -> CommitResult:
    """Stage + commit, stamping the active chat session as provenance trailers so
    `blame` can later attribute lines back to the conversation."""
    root = _repo_root(path_hint)
    if root is None:
        return CommitResult(ok=False, error="not a git repository")

    # Resolve the active conversation for provenance (lazy import: chat is optional).
    from backend.modules.chat.routes import _find, _read

    state = _read()
    sid = state.active
    title = None
    if sid:
        session = _find(state, sid)
        title = session.title if session else None

    add = _run(root, "add", *(["--", *paths] if paths else ["-A"]))
    if add.returncode != 0:
        return CommitResult(ok=False, error=add.stderr.strip() or "git add failed")

    full = message.rstrip("\n")
    if sid:
        full += f"\n\n{_SESSION_TRAILER}: {sid}"
        if title:
            full += f"\n{_TITLE_TRAILER}: {title}"

    res = _run(root, "commit", "-m", full)
    if res.returncode != 0:
        return CommitResult(
            ok=False,
            error=(res.stderr.strip() or res.stdout.strip() or "git commit failed"),
        )
    head = (_out(root, "rev-parse", "HEAD") or "").strip()
    return CommitResult(ok=True, sha=head[:8], session_id=sid, session_title=title)
