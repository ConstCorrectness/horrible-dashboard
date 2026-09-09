"""Pydantic models for the git provenance surface: blame (line → commit → session),
log, diff, and commit. See docs/modules/git.mdx."""

from __future__ import annotations

from pydantic import BaseModel


class BlameLine(BaseModel):
    line: int  # 1-based final line number
    commit: str  # short sha
    author: str
    summary: str
    session_id: str | None = None  # the agent conversation that authored this line
    session_title: str | None = None
    text: str | None = (
        None  # the line's content (trimmed), so the pane is self-contained
    )


class BlameResult(BaseModel):
    is_repo: bool
    path: str
    root: str | None = None
    lines: list[BlameLine] = []


class CommitInfo(BaseModel):
    sha: str  # short sha
    author: str
    date: str  # ISO 8601
    summary: str
    session_id: str | None = None  # set ⇒ agent-authored
    session_title: str | None = None


class LogResult(BaseModel):
    is_repo: bool
    commits: list[CommitInfo] = []


class DiffResult(BaseModel):
    sha: str
    diff: str


class CommitResult(BaseModel):
    ok: bool
    sha: str | None = None
    session_id: str | None = None
    session_title: str | None = None
    error: str | None = None


class CommitRequest(BaseModel):
    message: str
    paths: list[str] | None = None  # stage these (else stage everything)
    path: str | None = None  # a workspace path to locate the repo (else the first root)


class ScmEntry(BaseModel):
    """One changed path, with git's two status characters kept **apart**.

    `GitStatus` in the files module collapses them into one category for the file
    tree's decorations, which is right there and lossy here: source control's
    whole shape is staged vs unstaged, and a collapsed code cannot tell them
    apart."""

    path: str  # absolute
    index: str  # git's X character — the staged side
    worktree: str  # git's Y character — the working-tree side
    status: str  # the collapsed category, for the icon
    orig_path: str | None = None  # renames


class ScmStatus(BaseModel):
    """A repo's working tree, grouped the way source control is worked."""

    is_repo: bool
    root: str
    branch: str | None = None
    ahead: int = 0
    behind: int = 0
    staged: list[ScmEntry] = []
    unstaged: list[ScmEntry] = []
    untracked: list[ScmEntry] = []
    conflicted: list[ScmEntry] = []


class StageRequest(BaseModel):
    paths: list[str]
    path: str | None = None  # a workspace path to locate the repo


class StageResult(BaseModel):
    ok: bool
    error: str | None = None
