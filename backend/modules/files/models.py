from __future__ import annotations

from pydantic import BaseModel


class RootInfo(BaseModel):
    """One configured workspace root the tree is allowed to browse."""

    name: str
    path: str


class FileEntry(BaseModel):
    """A single file or directory. `path` is absolute (within a root)."""

    name: str
    path: str
    kind: str  # "file" | "dir"
    size: int | None = None
    mtime: float | None = None


class DirListing(BaseModel):
    path: str
    entries: list[FileEntry]


class FileContent(BaseModel):
    path: str
    content: str
    truncated: bool = False


class CreateRequest(BaseModel):
    path: str
    kind: str = "file"  # "file" | "dir"
    content: str = ""


class WriteRequest(BaseModel):
    path: str
    content: str


class RenameRequest(BaseModel):
    path: str
    new_path: str


class DeleteRequest(BaseModel):
    path: str
    recursive: bool = False


class OpResult(BaseModel):
    ok: bool
    path: str


class GitEntry(BaseModel):
    """One changed path in a repo's working tree. `path` is absolute (so it lines
    up with the tree's rows); `status` is a collapsed category."""

    path: str
    # modified | added | deleted | untracked | renamed | conflict
    status: str


class GitStatus(BaseModel):
    """The working-tree status of a workspace root, or `is_repo=False` if the root
    isn't inside a git repository."""

    is_repo: bool
    root: str
    branch: str | None = None
    entries: list[GitEntry] = []
