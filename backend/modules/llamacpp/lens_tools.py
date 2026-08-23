"""The `lens` agent tool group — reading a traced forward pass as *words*.

The Traces section made activations visible to a person. These make them readable
by the agent, which is the difference between a debugger and something that can
answer "why did it say that". The verbs are the same four the pane has: the grid,
one cell in depth, one vocabulary token tracked across the whole grid, and the
fork that changes a token and runs it again.

**Grouped**, so they cost nothing until loaded — the always-on core is 11 tools and
these would be four more schemas on every turn of every unrelated conversation.
The group name must equal the tool-name prefix, since `_group_of` splits on the
dot and `AgentTool.group` does not name the group (the connectors rule, and the
same trap `llamacpp` itself inverted).

Two containments worth stating, because both are places a tool could quietly
mislead:

- **An unverified grid is reported as unverified, never as numbers.** Every
  payload carries `verified` in the same three states the pane renders, and the
  `false` case leads with a warning rather than burying it under the cells. A
  model whose norm convention we get wrong produces confident wrong words, not an
  error, and an agent summarising those into prose is how a wrong reading becomes
  a claim.
- **`lens.fork` is a side effect.** It runs a real forward pass in a subprocess
  that mmaps the weights — minutes and gigabytes — and writes a new trace that
  counts against the trace budget, possibly pruning an older one. That is a
  decision for the permission gate, not for a turn that inferred it would help.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

#: A grid handed to a model is prose in a context window, not a screen. The pane
#: can render 48 x 200 cells and let an eye skip; a tool answer that size would
#: crowd out the reasoning that was supposed to use it.
MAX_TOOL_CELLS = 400


def _ids(raw: Any) -> list[int]:
    """A `layers`/`positions` argument, from a list or a comma-separated string.

    Models supply both shapes for an array parameter, and the string form is the
    one that silently becomes `[]` if only lists are accepted — the same failure
    `load_tools` had with a stringified array, where the tool ran successfully
    against nothing.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("[", "").replace("]", "").split(",")]
        return [int(p) for p in parts if p]
    if isinstance(raw, (list, tuple)):
        return [int(p) for p in raw]
    return [int(raw)]


def _require_trace(trace_id: str) -> Any:
    from backend.modules.llamacpp import traces

    if not trace_id:
        return None
    try:
        return traces.load(trace_id)
    except ValueError:
        return None


def _verdict(data: dict[str, Any]) -> dict[str, Any]:
    """The three-state self-check, phrased so it cannot be skimmed past."""
    verified = str(data.get("verified") or "unavailable")
    note = str(data.get("verifyNote") or "")
    if verified == "true":
        return {"verified": verified, "verifyNote": note}
    if verified == "false":
        return {
            "verified": verified,
            "warning": (
                "This lens did NOT reproduce the model's own captured logits, so "
                "every token below is suspect. Report that it could not be "
                "verified — do not present these as what the model was disposed "
                f"to say. ({note})"
            ),
        }
    return {
        "verified": verified,
        "warning": (
            "This trace captured no final logits, so the reading could not be "
            "checked against the model's own output. Treat it as unverified. "
            f"({note})"
        ),
    }


async def _grid(args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp import lens as lens_module

    trace = _require_trace(str(args.get("traceId", "")).strip())
    if trace is None:
        return {"error": "no such trace; call llamacpp.list_traces first"}
    layers = _ids(args.get("layers"))
    positions = _ids(args.get("positions"))
    k = max(1, min(int(args.get("k") or 3), 10))
    try:
        grid = await asyncio.to_thread(
            lens_module.compute_grid,
            trace,
            lens_id=str(args.get("lens") or "identity"),
            k=k,
            layers=layers,
            positions=positions,
        )
    except lens_module.LensError as exc:
        return {"error": str(exc)}

    data = grid.to_dict()
    rows: list[int] = list(data["layers"])
    cols: list[int] = list(data["positions"])
    if len(rows) * len(cols) > MAX_TOOL_CELLS:
        return {
            "error": (
                f"that grid is {len(rows)}x{len(cols)} cells, over the "
                f"{MAX_TOOL_CELLS} a tool answer can carry. Narrow it with "
                "`layers` or `positions` — the last few layers at the final "
                "position is usually the question."
            ),
            "layers": rows,
            "positions": cols,
        }
    tokens = _tokens_of(trace)
    return {
        "traceId": trace.trace_id,
        "prompt": str(trace.manifest.get("prompt") or ""),
        "lens": data["lens"],
        "layers": rows,
        "positions": cols,
        "promptTokens": [t.get("text", "") for t in tokens],
        # One row per layer, top-1 first. The full top-k is `lens.cell`'s job:
        # a k of 3 across 400 cells is 1200 strings, which is a wall.
        "top": [[_brief(cell) for cell in row] for row in data["cells"]],
        **_verdict(data),
    }


def _brief(cell: dict[str, Any] | None) -> Any:
    """One cell, small. `None` where the pass never computed that position."""
    if not cell:
        return None
    texts = list(cell.get("texts") or [])
    probs = list(cell.get("relProbs") or [])
    if not texts:
        return None
    return {"text": texts[0], "p": round(float(probs[0]), 3) if probs else None}


def _tokens_of(trace: Any) -> list[dict[str, Any]]:
    import json

    path = trace.directory / "tokens.json"
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


async def _cell(args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp import lens as lens_module

    trace = _require_trace(str(args.get("traceId", "")).strip())
    if trace is None:
        return {"error": "no such trace; call llamacpp.list_traces first"}
    try:
        layer = int(args["layer"])
        position = int(args["position"])
    except (KeyError, TypeError, ValueError):
        return {"error": "layer and position are both required integers"}
    try:
        grid = await asyncio.to_thread(
            lens_module.compute_grid,
            trace,
            lens_id=str(args.get("lens") or "identity"),
            k=max(1, min(int(args.get("k") or 20), 100)),
            layers=[layer],
            positions=[position],
        )
    except lens_module.LensError as exc:
        return {"error": str(exc)}
    data = grid.to_dict()
    cells = data["cells"]
    cell = cells[0][0] if cells and cells[0] else None
    if cell is None:
        return {
            "error": (
                f"layer {layer} has no reading at position {position} — llama.cpp "
                "did not compute that column there (the graph is pruned per pass, "
                "so the last block holds only the final position)"
            )
        }
    tokens = _tokens_of(trace)
    return {
        "traceId": trace.trace_id,
        "layer": layer,
        "position": position,
        "promptToken": (
            tokens[position].get("text") if position < len(tokens) else None
        ),
        "lens": data["lens"],
        "candidates": [
            {
                "id": cell["ids"][i],
                "text": cell["texts"][i],
                "logit": round(float(cell["logits"][i]), 4),
                "relProb": round(float(cell["relProbs"][i]), 4),
            }
            for i in range(len(cell.get("ids") or []))
        ],
        # Named, not implied: this is a softmax over the shown candidates, not
        # the model's distribution over its vocabulary.
        "relProbNote": "relProb is a softmax over these candidates only.",
        **_verdict(data),
    }


async def _track_token(args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp import lens as lens_module

    trace = _require_trace(str(args.get("traceId", "")).strip())
    if trace is None:
        return {"error": "no such trace; call llamacpp.list_traces first"}
    token_id = args.get("tokenId")
    # NOT stripped. A leading space is what makes " Paris" the token and "Paris"
    # not one, so stripping here deletes the very thing the error below tells
    # you to add — and every lookup of a real word fails while the message
    # advises the fix that was just undone. Whitespace *is* the token in a BPE
    # vocabulary; only a missing value counts as absent.
    text = str(args.get("text") or "")
    model_path = str(trace.manifest.get("modelPath") or "")
    if token_id is None:
        if not text:
            return {"error": "pass either tokenId or text"}
        try:
            resolved = await asyncio.to_thread(_lookup, model_path, text)
        except lens_module.LensError as exc:
            return {"error": str(exc)}
        if resolved is None:
            return {
                "error": (
                    f"{text!r} is not a single token in this model's vocabulary. "
                    "A lens tracks one vocabulary entry, so try a leading space "
                    "(' Paris' is usually the token, 'Paris' often is not)."
                )
            }
        token_id = resolved
    try:
        tracked = await asyncio.to_thread(
            lens_module.track_token,
            trace,
            int(token_id),
            lens_id=str(args.get("lens") or "identity"),
        )
    except lens_module.LensError as exc:
        return {"error": str(exc)}
    return {"traceId": trace.trace_id, **tracked}


def _lookup(model_path: str, text: str) -> int | None:
    """The vocabulary id whose rendered text is exactly `text`, if there is one."""
    from backend.modules.llamacpp import lens as lens_module

    un = lens_module.load_unembedding(model_path)
    for token_id, piece in enumerate(un.vocab):
        if lens_module.render_piece(piece, un.tokenizer_model) == text:
            return token_id
    return None


async def _fork(args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp import trace_runner
    from backend.modules.llamacpp.routes import _architecture, fork_spec

    trace = _require_trace(str(args.get("traceId", "")).strip())
    if trace is None:
        return {"error": "no such trace; call llamacpp.list_traces first"}
    raw_edits = args.get("edits")
    if isinstance(raw_edits, dict):
        raw_edits = [raw_edits]
    if not isinstance(raw_edits, list) or not raw_edits:
        return {"error": "edits is a non-empty list of {position, toId}"}
    try:
        spec = fork_spec(trace, [dict(e) for e in raw_edits])
    except (ValueError, TypeError, AttributeError) as exc:
        return {"error": str(exc)}

    spec["architecture"] = await asyncio.to_thread(
        _architecture, str(spec["modelPath"])
    )
    error = ""
    trace_id = ""
    async for event in trace_runner.run_trace(spec):
        if event.get("error"):
            error = str(event["error"])
        if event.get("status") == "stored":
            trace_id = str(event.get("traceId") or "")
    if error or not trace_id:
        return {"error": error or "the fork produced no trace"}
    # No catalog write here: `run_trace` records every trace it stores, forks
    # included. A second one is not wrong, it is a second place to forget.
    return {
        "traceId": trace_id,
        "derivedFrom": trace.trace_id,
        "edits": spec["edits"],
        "next": (
            "Call lens.grid on both trace ids and compare — the fork inherited "
            "every other setting, so they differ only where you edited."
        ),
    }


async def _focus(args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp import locus as lens_locus

    trace = _require_trace(str(args.get("traceId", "")).strip())
    locus: dict[str, Any] = {
        "traceId": str(args.get("traceId") or "") or None,
        "modelSha": str(trace.manifest.get("modelSha") or "") if trace else None,
        "layer": None if args.get("layer") is None else int(args["layer"]),
        "position": None if args.get("position") is None else int(args["position"]),
        "tokenId": None if args.get("tokenId") is None else int(args["tokenId"]),
    }
    return {"locus": lens_locus.set_locus(locus, source="agent")}


async def _save_finding(args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp import findings

    return await findings.save_finding(
        str(args.get("traceId", "")).strip(),
        note=str(args.get("note") or ""),
        library=str(args.get("library") or "default"),
        lens_id=str(args.get("lens") or "identity"),
        layers=_ids(args.get("layers")),
        positions=_ids(args.get("positions")),
    )


_TRACE_ID = {
    "type": "string",
    "description": "Trace id from llamacpp.list_traces.",
}
_LENS = {
    "type": "string",
    "description": "Lens id; 'identity' (the classic logit lens) is always available.",
}

LENS_TOOLS: list[AgentTool] = [
    AgentTool(
        name="lens.grid",
        description=(
            "Read a traced forward pass as words: the top token the model was "
            "disposed to say at every layer and prompt position. Narrow it with "
            "layers/positions — the last few layers at the final position is "
            "usually the question. Reports whether the reading was verified "
            "against the model's own logits."
        ),
        parameters={
            "traceId": _TRACE_ID,
            "lens": _LENS,
            "k": {
                "type": "integer",
                "description": "Candidates per cell (1-10, default 3).",
            },
            "layers": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Layer numbers to include; -1 is the embedding. Omit for all.",
            },
            "positions": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Token positions to include. Omit for all.",
            },
        },
        required=["traceId"],
        handler=_grid,
        group="lens",
    ),
    AgentTool(
        name="lens.cell",
        description=(
            "One (layer, position) in depth: the full ranked candidate list with "
            "logits, rather than the single top token lens.grid shows."
        ),
        parameters={
            "traceId": _TRACE_ID,
            "layer": {
                "type": "integer",
                "description": "Layer number; -1 is the embedding.",
            },
            "position": {"type": "integer", "description": "Token position."},
            "lens": _LENS,
            "k": {"type": "integer", "description": "Candidates (1-100, default 20)."},
        },
        required=["traceId", "layer", "position"],
        handler=_cell,
        group="lens",
    ),
    AgentTool(
        name="lens.track_token",
        description=(
            "Follow one vocabulary token's rank and logit across every cell of the "
            "grid — how a word climbs (or never appears) through the layers. Give "
            "either tokenId or the exact text of a single token."
        ),
        parameters={
            "traceId": _TRACE_ID,
            "tokenId": {"type": "integer", "description": "Vocabulary id."},
            "text": {
                "type": "string",
                "description": "Exact token text, if the id is unknown. Usually needs a leading space.",
            },
            "lens": _LENS,
        },
        required=["traceId"],
        handler=_track_token,
        group="lens",
    ),
    AgentTool(
        name="lens.fork",
        description=(
            "Change one or more prompt tokens and run the trace again, inheriting "
            "every other setting so the two are comparable. The counterfactual: "
            "what does swapping this word do to what the model was going to say. "
            "Runs a real forward pass — slow, and it writes a new trace."
        ),
        parameters={
            "traceId": _TRACE_ID,
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "toId": {"type": "integer"},
                    },
                },
                "description": "Replacements: [{position, toId}]. toId is a vocabulary id.",
            },
        },
        required=["traceId", "edits"],
        side_effect=True,
        specifier_template="{traceId}",
        handler=_fork,
        group="lens",
    ),
    AgentTool(
        name="lens.focus",
        description=(
            "Point the user's panes at part of the model: a layer reveals that "
            "block's tensors in the model explorer, and the lens grid follows the "
            "cell. Use it while explaining, so the screen shows what you mean."
        ),
        parameters={
            "traceId": _TRACE_ID,
            "layer": {
                "type": "integer",
                "description": "Layer to reveal; -1 is the embedding.",
            },
            "position": {"type": "integer", "description": "Token position."},
            "tokenId": {"type": "integer", "description": "Vocabulary token to pin."},
        },
        handler=_focus,
        group="lens",
    ),
    AgentTool(
        name="lens.save_finding",
        description=(
            "Write a lens reading into the knowledge library as a note — the "
            "prompt, what the model was disposed to say per layer, and the "
            "provenance to reproduce it. Refuses to file an unverified grid."
        ),
        parameters={
            "traceId": _TRACE_ID,
            "note": {
                "type": "string",
                "description": "What you concluded. Saved above the table.",
            },
            "library": {
                "type": "string",
                "description": "Library name (default 'default').",
            },
            "lens": _LENS,
            "layers": {"type": "array", "items": {"type": "integer"}},
            "positions": {"type": "array", "items": {"type": "integer"}},
        },
        required=["traceId"],
        side_effect=True,
        specifier_template="{traceId}",
        handler=_save_finding,
        group="lens",
    ),
]


def register_lens_tools() -> None:
    from backend.sdk.registry import registry

    for tool in LENS_TOOLS:
        registry.agent_tools[tool.name] = tool
