"""Repo-wide **text search** across the workspace roots.

The IDE's Search panel and the `ide.findInFiles` agent tool run through here. It
lives in the files module because the workspace-root boundary is the whole
security story of this feature and that boundary lives in `routes.py`.

**Two engines, one result shape.** ripgrep answers when it is installed, and a
pure-Python walk answers when it is not. ripgrep is *supported, never bundled* —
it is not a declared dependency, and the house rule (SearXNG, Chromium, the geoip
database) is that a third-party binary is detected on the machine rather than
shipped. Which one answered is reported in `engine`, because a search that took
four seconds instead of forty milliseconds should say why.

**Three states, not two.** Like the hardware probe, a result distinguishes "no
matches" from "we stopped early": `truncated` means the result cap was reached and
`timed_out` means the wall clock was. Reporting either as an empty tail is how a
user concludes a string is absent from a repo that contains it.

Three safety concerns, none of which the ripgrep invocation handles for you:

1. **Root containment.** Every returned path is re-checked against the roots
   *after* the subprocess returns. The boundary must not depend on a
   subprocess's flags.
2. **Argument injection.** The query is an argv element behind ``--`` and the
   process runs with ``shell=False``, so a query beginning with ``-`` is a query
   rather than a flag.
3. **Virtual roots.** A provider path (``gdrive:/…``) is refused with a message
   rather than being handed to `_resolve`, which anchors relative paths and would
   turn it into a nonsense 403.

See docs/modules/ide.mdx.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from backend.modules.files.models import (
    SearchFileResult,
    SearchMatch,
    SearchRequest,
    SearchResult,
)

# Directories never worth walking. Pruned from `dirnames` **in place** during the
# walk, not filtered afterwards — filtering after descending is how a search over
# a monorepo with node_modules takes forty seconds.
DEFAULT_EXCLUDES = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".data",
    "target",
)

# A matching line is trimmed before it crosses the wire: one 4 MB minified line
# would otherwise be a result row.
MAX_LINE_CHARS = 400
_RG_TIMEOUT_S = 15.0
# The Python walk's budget. Deliberately shorter than the ripgrep timeout: the
# fallback is the slow path, and a user waiting on a sidebar wants an honest
# partial answer sooner than a complete one late.
_WALK_BUDGET_S = 8.0


def _rg_path() -> str | None:
    """The ripgrep binary, if the machine has one. Its own function so a test can
    force the pure-Python leg by patching it."""
    return shutil.which("rg")


def _pattern(req: SearchRequest) -> re.Pattern[str] | None:
    """The compiled query, or None when it is not valid regex."""
    source = req.query if req.regex else re.escape(req.query)
    if req.whole_word:
        source = rf"\b(?:{source})\b"
    try:
        return re.compile(source, 0 if req.case_sensitive else re.IGNORECASE)
    except re.error:
        return None


def _matches_globs(rel: str, globs: list[str]) -> bool:
    """Whether a root-relative path matches any of the globs. Both the full
    relative path and the bare filename are tried, so `*.ts` works without the
    user having to write `**/*.ts`."""
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in globs)


def _trim(line: str, start: int, end: int) -> tuple[str, int, int]:
    """Trim a long line around its match, keeping the offsets consistent with the
    text actually returned. A caller highlights `[start, end)` of `text`, so
    returning an untrimmed offset into a trimmed string paints the wrong span."""
    line = line.rstrip("\n\r")
    if len(line) <= MAX_LINE_CHARS:
        return line, start, end
    # Keep a little context before the match rather than always the head of the
    # line: a match at column 900 of a long line is invisible otherwise.
    lead = max(0, start - 60)
    return (
        line[lead : lead + MAX_LINE_CHARS],
        start - lead,
        min(end - lead, MAX_LINE_CHARS),
    )


def _inside_roots(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def search_text(req: SearchRequest, roots: list[Path]) -> SearchResult:
    """Run a search over `roots`. Blocking — call it through a worker thread."""
    if not roots:
        return SearchResult(engine="none", error="no workspace roots configured")
    if not req.query:
        return SearchResult(engine="none")
    if req.regex and _pattern(req) is None:
        return SearchResult(engine="none", error="invalid regular expression")

    rg = _rg_path()
    if rg:
        result = _search_ripgrep(rg, req, roots)
        # A ripgrep that failed to start or crashed is not an answer; fall back
        # rather than reporting an empty repo.
        if result is not None:
            return result
    return _search_python(req, roots)


# ----------------------------------------------------------------- ripgrep --


def _rg_argv(rg: str, req: SearchRequest, roots: list[Path]) -> list[str]:
    argv = [
        rg,
        "--json",
        "--line-number",
        "--column",
        "--no-heading",
        "--no-messages",
        f"--max-filesize={req.max_file_bytes}",
    ]
    if not req.regex:
        argv.append("--fixed-strings")
    if not req.case_sensitive:
        argv.append("--ignore-case")
    if req.whole_word:
        argv.append("--word-regexp")
    for glob in req.include:
        argv += ["-g", glob]
    for glob in list(req.exclude) + [f"{name}/" for name in DEFAULT_EXCLUDES]:
        argv += ["-g", f"!{glob}"]
    # `--` separates flags from operands, so a query starting with `-` is a query.
    argv.append("--")
    argv.append(req.query)
    argv.extend(str(root) for root in roots)
    return argv


def _search_ripgrep(
    rg: str, req: SearchRequest, roots: list[Path]
) -> SearchResult | None:
    """Search with ripgrep. None means it could not answer — the caller falls back
    to the Python walk rather than reporting no matches."""
    try:
        proc = subprocess.run(
            _rg_argv(rg, req, roots),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RG_TIMEOUT_S,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return SearchResult(engine="ripgrep", timed_out=True)
    except (OSError, subprocess.SubprocessError):
        return None
    # 0 = matches, 1 = none. Anything else is a real failure (bad regex, unreadable
    # tree), and the Python walk gets its own chance to say so precisely.
    if proc.returncode not in (0, 1):
        return None

    files: dict[str, list[SearchMatch]] = {}
    total = 0
    truncated = False
    for line in proc.stdout.splitlines():
        if total >= req.max_results:
            truncated = True
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path_text = (data.get("path") or {}).get("text")
        if not path_text:
            # A path ripgrep could not decode arrives base64 instead. Skipping is
            # right: we cannot re-check it against the roots, and a result we
            # cannot bound is one we must not return.
            continue
        resolved = Path(path_text)
        # The containment re-check. ripgrep does not follow symlinks by default,
        # but the boundary is ours to enforce, not a flag's.
        if not _inside_roots(resolved, roots):
            continue
        line_text = (data.get("lines") or {}).get("text") or ""
        line_no = int(data.get("line_number") or 0)
        for submatch in data.get("submatches", []) or []:
            if total >= req.max_results:
                truncated = True
                break
            start = int(submatch.get("start", 0))
            end = int(submatch.get("end", start))
            # ripgrep reports byte offsets into the line; convert to characters so
            # the highlight lands correctly on a line containing any non-ASCII.
            raw = line_text.encode("utf-8", "replace")
            col_start = len(raw[:start].decode("utf-8", "replace"))
            col_end = len(raw[:end].decode("utf-8", "replace"))
            text, hl_start, hl_end = _trim(line_text, col_start, col_end)
            files.setdefault(str(resolved), []).append(
                SearchMatch(
                    path=str(resolved),
                    line=line_no,
                    column=col_start + 1,
                    text=text,
                    match_start=hl_start,
                    match_end=hl_end,
                )
            )
            total += 1

    return SearchResult(
        engine="ripgrep",
        files=[SearchFileResult(path=p, matches=m) for p, m in files.items()],
        total=total,
        truncated=truncated,
    )


# ------------------------------------------------------------------ python --


def _search_python(req: SearchRequest, roots: list[Path]) -> SearchResult:
    pattern = _pattern(req)
    if pattern is None:
        return SearchResult(engine="python", error="invalid regular expression")

    excludes = set(DEFAULT_EXCLUDES)
    deadline = time.monotonic() + _WALK_BUDGET_S
    files: list[SearchFileResult] = []
    total = 0
    truncated = False
    timed_out = False

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                timed_out = True
                break
            # In place, so the walk never descends into them at all.
            dirnames[:] = [
                d for d in dirnames if d not in excludes and not d.startswith(".")
            ]
            for filename in filenames:
                if total >= req.max_results:
                    truncated = True
                    break
                path = Path(dirpath) / filename
                rel = path.relative_to(root).as_posix()
                if req.include and not _matches_globs(rel, req.include):
                    continue
                if req.exclude and _matches_globs(rel, req.exclude):
                    continue
                matches = _scan_file(
                    path, pattern, req.max_file_bytes, req.max_results - total
                )
                if matches:
                    files.append(SearchFileResult(path=str(path), matches=matches))
                    total += len(matches)
            if truncated or timed_out:
                break
        if truncated or timed_out:
            break

    return SearchResult(
        engine="python",
        files=files,
        total=total,
        truncated=truncated,
        timed_out=timed_out,
    )


def _scan_file(
    path: Path, pattern: re.Pattern[str], max_bytes: int, budget: int
) -> list[SearchMatch]:
    """Every match in one file, up to `budget`. A file that is too large, binary,
    or unreadable simply contributes nothing — a search must not fail because one
    file in the tree is a JPEG."""
    try:
        if path.stat().st_size > max_bytes:
            return []
    except OSError:
        return []
    out: list[SearchMatch] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for number, line in enumerate(handle, start=1):
                if len(out) >= budget:
                    break
                for found in pattern.finditer(line):
                    if len(out) >= budget:
                        break
                    text, start, end = _trim(line, found.start(), found.end())
                    out.append(
                        SearchMatch(
                            path=str(path),
                            line=number,
                            column=found.start() + 1,
                            text=text,
                            match_start=start,
                            match_end=end,
                        )
                    )
    except (OSError, UnicodeDecodeError, ValueError):
        return out
    return out
