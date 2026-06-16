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
