"""Saving a lens reading into the knowledge library — the `browser.save` precedent.

A grid is a picture on a screen that goes away when the pane closes, and a trace is
pruned the moment the budget wants its bytes. What survives is what got written
down, so this renders a lens grid (plus whatever the reader concluded) as a `note`
source in a library: searchable, citable in RAG, and joinable to everything else
the node knows.

Deliberately a **note and not an artifact**. The activations are not copied —
megabytes of fp16 have no business being embedded, and the numbers are only
meaningful next to the weights that produced them. What is written down is the
*reading*: the prompt, the tokens, what the model was disposed to say at each
layer, and the provenance needed to reproduce it (model sha, llama build, trace id,
and any edits, so a fork's note says what was changed). The trace id is recorded
even though the trace may later be pruned — a note saying "trace 20260823-… of
model sha abc, since pruned" is strictly better than one that omits it, and
`traces.matches_run` is the rule for whether it may be overlaid on anything.

An **unverified grid is refused**, not saved with a caveat. The self-check exists
because per-architecture norm conventions get silently-wrong numbers rather than
errors; a caveat in a note body survives exactly as far as the first RAG retrieval
that quotes the numbers without it.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.llamacpp import lens as lens_module
from backend.modules.llamacpp import traces

logger = logging.getLogger(__name__)

#: How many positions get their own column in the rendered table. A note is prose
#: for a person and a chunk for an embedder; a 200-column table is neither.
MAX_POSITIONS = 24


def render_note(
    trace: traces.Trace,
    grid: lens_module.LensGrid,
    *,
    note: str = "",
) -> tuple[str, str]:
    """`(title, markdown)` for one lens reading. Pure — the test's whole surface."""
    m = trace.manifest
    model = str(m.get("modelName") or "the model")
    prompt = str(m.get("prompt") or "")
    data = grid.to_dict()
    layers: list[int] = list(data.get("layers") or [])
    positions: list[int] = list(data.get("positions") or [])
    cells: list[list[dict[str, Any]]] = list(data.get("cells") or [])
    edits = list(m.get("edits") or [])

    title = f"Lens reading — {model}: {_snippet(prompt)}"
    lines: list[str] = [f"# {title}", ""]
    if note.strip():
        lines += [note.strip(), ""]

    lines += [
        "## Reading",
        "",
        f"- **Model**: `{model}` (sha `{m.get('modelSha') or '—'}`)",
        f"- **Lens**: {data.get('lens', {}).get('label') or 'identity'}",
        f"- **Trace**: `{trace.trace_id}`, llama.cpp build `{m.get('llamaBuild') or '—'}`",
        f"- **Verified**: {data.get('verified')} — {data.get('verifyNote') or ''}".rstrip(
            " —"
        ),
    ]
    if edits:
        parent = m.get("derivedFrom") or "?"
        described = ", ".join(
            f"position {e.get('position')}: {e.get('fromId')} → {e.get('toId')}"
            for e in edits
        )
        lines.append(f"- **Forked from** `{parent}` with {described}")
    lines += ["", "## Prompt", "", "```", prompt or "(no prompt recorded)", "```", ""]

    shown = positions[:MAX_POSITIONS]
    if shown and layers and cells:
        lines += ["## Top token per layer", ""]
        header = " | ".join(str(p) for p in shown)
        lines += [f"| layer | {header} |", "| --- | " + " --- |" * len(shown)]
        for row_index, layer in enumerate(layers):
            row = cells[row_index] if row_index < len(cells) else []
            texts: list[str] = []
            for column, _ in enumerate(shown):
                # `or {}` and not a length check alone: a cell is `None` wherever
                # llama.cpp never computed that position at that layer (the rows
                # are ragged — the last block holds one column), and a missing
                # reading must render as "—" rather than as the previous token.
                cell = (row[column] if column < len(row) else None) or {}
                candidates = list(cell.get("texts") or [])
                texts.append(f"`{candidates[0]}`" if candidates else "—")
            lines.append(f"| {_layer_label(layer)} | {' | '.join(texts)} |")
        if len(positions) > len(shown):
            lines += [
                "",
                f"_{len(positions) - len(shown)} further positions are in the trace "
                "but not in this note._",
            ]
        lines.append("")

    lines += [
        "---",
        "",
        "Written by the llama.cpp lens. The activations themselves stay on the node "
        "that produced them; this is the reading, not the tensors.",
    ]
    return title, "\n".join(lines)


def _layer_label(layer: int) -> str:
    """`-1` is the embedding and the top layer is the model's own output — both
    read as a wrong layer number if rendered as a bare integer."""
    if layer == lens_module.EMBEDDING_LAYER:
        return "embed"
    return str(layer)


def _snippet(prompt: str, limit: int = 60) -> str:
    text = " ".join(prompt.split())
    if not text:
        return "(no prompt)"
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


async def save_finding(
    trace_id: str,
    *,
    note: str = "",
    library: str = "default",
    lens_id: str = "identity",
    k: int = 5,
    layers: list[int] | None = None,
    positions: list[int] | None = None,
    pass_index: int = 0,
) -> dict[str, Any]:
    """Compute a grid and file it as a `note` source. Returns the source row."""
    from backend.modules.library import store as library_store
    from backend.modules.library.ingest import ingest_source
    from backend.modules.library.models import IngestRequest

    trace = traces.load(trace_id)
    if trace is None:
        return {"error": f"no trace {trace_id}"}
    try:
        grid = lens_module.compute_grid(
            trace,
            lens_id=lens_id,
            k=max(1, min(k, 100)),
            layers=list(layers or []),
            positions=list(positions or []),
            pass_index=pass_index,
        )
    except lens_module.LensError as exc:
        return {"error": str(exc)}

    verified = grid.to_dict().get("verified")
    if verified != "true":
        # See the module docstring: a caveat does not survive retrieval.
        return {
            "error": (
                f"this grid is {verified} against the model's own captured logits, "
                "so it is not a finding yet — only a verified reading is worth "
                "filing. Re-run the trace with `result_output` captured, or fix "
                "what the self-check is reporting."
            ),
            "verified": verified,
            "verifyNote": grid.to_dict().get("verifyNote") or "",
        }

    title, markdown = render_note(trace, grid, note=note)
    source = library_store.create_source(
        library=library,
        type="note",
        title=title,
        url=None,
        author="llama.cpp lens",
        tags=[
            tag
            for tag in (
                "lens",
                "interpretability",
                str(trace.manifest.get("modelName") or ""),
            )
            if tag
        ],
    )
    # Awaited rather than queued: the caller is a tool or a button that wants to
    # say "saved", and `enqueue_task` is serial — a library ingest parked behind
    # a multi-minute job would report success for a source with no chunks.
    await ingest_source(
        source["id"],
        IngestRequest(type="note", library=library, text=markdown, title=title),
    )

    # `ingest_source` does not raise: it records the failure on the row and
    # returns, so awaiting it proves the pipeline *ran*, not that it worked. A
    # source can end up `failed` with zero chunks — an embedding-width mismatch
    # against an existing collection is the common one — and reporting "saved"
    # for a note that no search will ever return is exactly the silent success
    # this whole surface refuses elsewhere. So read the row back and say so.
    stored = library_store.get_source(source["id"]) or {}
    status = str(stored.get("status") or "")
    if status == "failed":
        return {
            "sourceId": source["id"],
            "library": library,
            "title": title,
            "traceId": trace.trace_id,
            "chars": len(markdown),
            "error": (
                "the note was created but could not be indexed, so nothing will "
                f"retrieve it: {stored.get('error') or 'unknown ingestion error'}"
            ),
        }
    return {
        "sourceId": source["id"],
        "library": library,
        "title": title,
        "traceId": trace.trace_id,
        "chars": len(markdown),
        "chunks": int(stored.get("chunk_count") or 0),
    }
