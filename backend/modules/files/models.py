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
    # The display name, when the path isn't one. A virtual path carries an opaque id
    # (`gdrive:/1a2b3c`), so the editor has no other way to title the buffer.
    name: str | None = None


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


class SearchRequest(BaseModel):
    """A repo-wide text search. POST rather than GET because a regex is full of
    `&`, `#` and `+`, which makes it a query-string encoding minefield."""

    query: str
    # A specific workspace root, or every configured root when omitted.
    root: str | None = None
    regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    # Glob filters. Matched against both the root-relative path and the bare
    # filename, so `*.ts` works without writing `**/*.ts`.
    include: list[str] = []
    exclude: list[str] = []
    max_results: int = 500
    max_file_bytes: int = 2_000_000


class SearchMatch(BaseModel):
    """One hit. `line`/`column` are 1-based (what an editor shows); `match_start`
    and `match_end` are 0-based character offsets **into `text`**, which may have
    been trimmed — so a caller highlights the right span of what it received."""

    path: str
    line: int
    column: int
    text: str
    match_start: int
    match_end: int


class SearchFileResult(BaseModel):
    path: str
    matches: list[SearchMatch] = []


class SearchResult(BaseModel):
    """Results, plus honesty about how they were produced.

    `engine` is `ripgrep`, `python` or `none`. `truncated` and `timed_out` are
    separate from an empty tail on purpose: "we found no more" and "we stopped
    looking" are different facts, and collapsing them is how a user concludes a
    string is absent from a repo that contains it."""

    engine: str
    files: list[SearchFileResult] = []
    total: int = 0
    truncated: bool = False
    timed_out: bool = False
    error: str | None = None
