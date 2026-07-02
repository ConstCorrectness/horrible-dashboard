"""The `@@HORRIBLE@@{json}` stdout sentinel protocol.

Training code (via the `horrible-train` helper) emits one JSON event per stdout
line, prefixed with the sentinel. The backend strips those lines from the visible
output and fans them out as `training` channel events — the same line-protocol
trick the visualizer uses for Pygame frames, applied uniformly to kernel cells,
script runs, and manim renders.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SENTINEL = "@@HORRIBLE@@"

# helper event type → ws event name (everything else is dropped with a debug log).
EVENT_NAMES = {
    "run": "run_started",
    "metric": "metrics",
    "frame": "frame",
    "model_graph": "model_graph",
    "model_stats": "model_stats",
}


def parse_line(line: str) -> dict[str, Any] | None:
    """The event dict for a sentinel line, or None if it isn't one / is invalid."""
    if not line.startswith(SENTINEL):
        return None
    try:
        payload = json.loads(line[len(SENTINEL) :])
    except ValueError:
        logger.debug("bad sentinel line ignored: %.80s", line)
        return {}  # was a sentinel line (strip it) but carried nothing usable
    return payload if isinstance(payload, dict) else {}


class LineSplitter:
    """Stateful splitter for one output stream: separates sentinel events from
    passthrough text across arbitrary chunk boundaries.

    Complete non-sentinel lines (and carriage-return updates) pass through
    immediately; a trailing partial line is only withheld when it could still
    grow into a sentinel marker, so ordinary partial prints (progress bars,
    `print(..., end='')`) stay live.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> tuple[str, list[dict[str, Any]]]:
        """Returns (passthrough_text, events) for this chunk."""
        text = self._buf + chunk
        self._buf = ""
        out: list[str] = []
        events: list[dict[str, Any]] = []
        while text:
            nl = text.find("\n")
            if nl == -1:
                # Partial tail (the start of an unfinished line): hold it back
                # only while it could still grow into a sentinel line.
                tail = text.lstrip("\r")
                if tail.startswith(SENTINEL) or (
                    len(tail) < len(SENTINEL) and SENTINEL.startswith(tail)
                ):
                    self._buf = text
                else:
                    out.append(text)
                break
            line, text = text[: nl + 1], text[nl + 1 :]
            stripped = line.lstrip("\r").rstrip("\n")
            event = parse_line(stripped)
            if event is None:
                out.append(line)
            elif event:
                events.append(event)
        return "".join(out), events

    def flush(self) -> str:
        """Any withheld partial text (stream ended without a newline)."""
        text, self._buf = self._buf, ""
        return "" if text.startswith(SENTINEL) else text
