"""Atomic file replacement that also works on Windows.

Every file this backend treats as a record — a training `project.json`, a saved
`.ipynb` — is written by one thread while another may be reading it. `write_text`
truncates before it writes, so a read landing in that window gets an empty file, and
the caller's `except ValueError: return None` turns that into "the record does not
exist". That is how a training project answered **404 while it existed**.

Writing to a temp file in the same directory and `os.replace`-ing over the target
fixes the reader's view — but `os.replace` on Windows is `MoveFileEx`, which fails
with `PermissionError` (WinError 5) if the destination is *open*, and CPython's
`open()` does not pass `FILE_SHARE_DELETE`. So on Windows the naive atomic write
does not remove the race, it moves it from the reader to the writer.

Hence the retry: the reader's hold is measured in microseconds, so a handful of
short backoffs covers it. The temp file must share the destination's directory —
`os.replace` is only atomic within a filesystem, and `%TEMP%` is routinely a
different volume.

The reader needs the same treatment, and this is the part that is easy to miss:
while `MoveFileEx` is replacing the destination, opening it raises
`PermissionError` (the old entry is delete-pending). So `read_text_or_none`
distinguishes **"this record does not exist"** — the answer callers turn into a 404
— from **"someone is replacing it right now"**, which is not an answer at all.
Conflating the two is the original bug wearing a different hat.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

#: Retry budget for the replace step. Generous against a reader that holds the file
#: open for microseconds; short enough that a genuine lock (an editor, a virus
#: scanner) still surfaces as an error rather than a stall.
REPLACE_ATTEMPTS = 10
REPLACE_BACKOFF_S = 0.02


def replace_with_retry(src: str | Path, dst: str | Path) -> None:
    """`os.replace`, retried past a concurrent reader on Windows."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_S)


def read_text_or_none(path: Path) -> str | None:
    """Read `path`, or None if it genuinely is not there.

    Retries the transient `PermissionError` Windows raises while the file is being
    replaced — see the module docstring. A file that is missing on every attempt is
    reported as missing; anything else is raised.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_S)
    return None


def write_text_atomic(path: Path, text: str, suffix: str = ".tmp") -> None:
    """Write `text` to `path` so no reader ever observes a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
