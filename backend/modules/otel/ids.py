"""Trace ids derived from `turn_id` — so the two can never disagree.

`turn_id` is the trace id everywhere in this codebase (trajectories, interpretability,
evals, telemetry). OTel needs a 128-bit trace id, and the tempting move is to mint
one per turn and store it in a new column. That would be a second identity for the
same thing, and a join that only works if every writer remembered to fill it in.
Instead the trace id is a pure function of the turn id: anything holding a
`turn_id` can compute the trace, and a span arriving from a user's MCP server with
that trace id can be attributed to its turn without a lookup table.

Delegate sub-turns are `"<parent>:<spec>:<hex>"` (`agent/delegate.py`). Hashing only
the part before the first `:` puts them in their parent's trace, which is exactly
what `parent_run_id` already says about them.
"""

from __future__ import annotations

import hashlib


def root_turn(turn_id: str) -> str:
    """The turn a (possibly delegated) turn id belongs to."""
    return turn_id.split(":", 1)[0]


def trace_id_for_turn(turn_id: str) -> str:
    """32 lowercase hex chars (128 bits), stable for a turn and its delegates."""
    digest = hashlib.sha256(root_turn(turn_id).encode("utf-8")).hexdigest()[:32]
    # An all-zero trace id is invalid in OTel. Astronomically unlikely, but a
    # function that can emit an id every exporter drops should not exist.
    return digest if digest.strip("0") else "0" * 31 + "1"


def trace_id_int(turn_id: str) -> int:
    return int(trace_id_for_turn(turn_id), 16)
