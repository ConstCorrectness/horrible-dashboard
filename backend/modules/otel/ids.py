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

A fork (`agentpedia/fork.py`) is `"<original>:fork:<hex>"`, and is the one
exception: it is a *new* run of an old turn, started later, by someone else. Folded
into the original's trace it would be a second root there — and an agent-trace UI
like Opik builds the trace record from a root span, so the fork would overwrite the
original's name and input. So `:fork:<hex>` is a trace boundary: each fork is its
own trace, and the fork's delegates (`"<original>:fork:<hex>:<spec>:<hex>"`) join it.
"""

from __future__ import annotations

import hashlib

_FORK = ":fork:"


def root_turn(turn_id: str) -> str:
    """The turn a (possibly delegated) turn id belongs to."""
    head, sep, rest = turn_id.partition(_FORK)
    if sep:
        # "<original>:fork:<hex>" — the fork is the root, whatever hangs off it.
        return f"{head.split(':', 1)[0]}{_FORK}{rest.split(':', 1)[0]}"
    return turn_id.split(":", 1)[0]


def trace_id_for_turn(turn_id: str) -> str:
    """32 lowercase hex chars (128 bits), stable for a turn and its delegates."""
    digest = hashlib.sha256(root_turn(turn_id).encode("utf-8")).hexdigest()[:32]
    # An all-zero trace id is invalid in OTel. Astronomically unlikely, but a
    # function that can emit an id every exporter drops should not exist.
    return digest if digest.strip("0") else "0" * 31 + "1"


def trace_id_int(turn_id: str) -> int:
    return int(trace_id_for_turn(turn_id), 16)
