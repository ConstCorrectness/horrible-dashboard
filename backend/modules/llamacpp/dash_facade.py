"""`dash.lens` — scripting the lens from the REPL.

The pane is for looking and the agent tools are for asking; this is for **sweeps**.
"Which layer does the answer first appear at, across these forty prompts" is a loop
over `grid()` and a `max()`, and it is not a thing a chat turn or a click can do at
all. That is why the facade exists rather than being a third spelling of the same
four verbs.

A **backend-local** facade, like `dash.io` and `dash.settings` and unlike
`dash.panes`: everything here reads the node's own disk, so it needs no browser
attached and no relay round trip. The one exception is `focus()`, which is a push
*to* browsers — and it still works with none, because the locus is stored and the
next window to attach follows it.

Blocking calls on the REPL's worker thread, deliberately: `compute_grid` streams a
262k-row output head, so a sweep of it is seconds of real work, and pretending
otherwise with an async surface would only make the REPL harder to write in.
"""

from __future__ import annotations

from typing import Any


class _Lens:
    """Read traced forward passes as words, and script sweeps over them."""

    def traces(
        self, limit: int = 20, model_sha: str = "", derived_from: str = ""
    ) -> list[dict[str, Any]]:
        """Stored traces, newest first (the `llamacpp_traces` catalog in app.db)."""
        from backend.modules.llamacpp import trace_catalog

        return trace_catalog.rows(
            limit=limit, model_sha=model_sha, derived_from=derived_from
        )

    def lenses(self, trace_id: str) -> list[dict[str, Any]]:
        """Which lenses apply to a trace's model; `identity` is always present."""
        from backend.modules.llamacpp import lens as lens_module
        from backend.modules.llamacpp import traces

        trace = traces.load(trace_id)
        if trace is None:
            raise ValueError(f"no trace {trace_id}")
        sha = str(trace.manifest.get("modelSha") or "")
        return [spec.to_dict() for spec in lens_module.available_lenses(sha)]

    def grid(
        self,
        trace_id: str,
        *,
        lens: str = "identity",
        k: int = 5,
        layers: list[int] | None = None,
        positions: list[int] | None = None,
        pass_index: int = 0,
    ) -> dict[str, Any]:
        """The layer x position readout. Carries `verified` — check it before
        believing the cells."""
        from backend.modules.llamacpp import lens as lens_module
        from backend.modules.llamacpp import traces

        trace = traces.load(trace_id)
        if trace is None:
            raise ValueError(f"no trace {trace_id}")
        grid = lens_module.compute_grid(
            trace,
            lens_id=lens,
            k=k,
            layers=list(layers or []),
            positions=list(positions or []),
            pass_index=pass_index,
        )
        return grid.to_dict()

    def track(
        self,
        trace_id: str,
        token_id: int,
        *,
        lens: str = "identity",
        pass_index: int = 0,
    ) -> dict[str, Any]:
        """One vocabulary token's rank and logit at every cell — the token pin."""
        from backend.modules.llamacpp import lens as lens_module
        from backend.modules.llamacpp import traces

        trace = traces.load(trace_id)
        if trace is None:
            raise ValueError(f"no trace {trace_id}")
        return lens_module.track_token(
            trace, int(token_id), lens_id=lens, pass_index=pass_index
        )

    def vocab(self, trace_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search the traced model's own vocabulary — what a swap must name."""
        from backend.modules.llamacpp import lens as lens_module
        from backend.modules.llamacpp import traces

        trace = traces.load(trace_id)
        if trace is None:
            raise ValueError(f"no trace {trace_id}")
        un = lens_module.load_unembedding(str(trace.manifest.get("modelPath") or ""))
        out: list[dict[str, Any]] = []
        for token_id, piece in enumerate(un.vocab):
            text = lens_module.render_piece(piece, un.tokenizer_model)
            if query and query not in text and query not in piece:
                continue
            out.append({"id": token_id, "piece": piece, "text": text})
            if len(out) >= limit:
                break
        return out

    # -- the stepper's reads, as arrays -------------------------------------
    #
    # The pane steps one record at a time; a notebook wants the same reads as
    # numpy so it can loop over them ("which head attends to the first token in
    # every layer"). Same functions the routes call — one implementation.

    def program(self, trace_id: str, pass_index: int = 0) -> list[dict[str, Any]]:
        """The records of one pass in execution order — what the stepper steps."""
        trace = _load(trace_id)
        return [r.to_dict() for r in trace.records if r.pass_index == pass_index]

    def tensor(self, trace_id: str, name: str, pass_index: int = 0) -> Any:
        """One record as a numpy array in `[ne3, ne2, ne1, ne0]` order, strides
        honoured (`l_out-12`, `kq_soft_max-3`, …)."""
        from backend.modules.llamacpp import stepper

        trace = _load(trace_id)
        for record in trace.records:
            if record.name == name and record.pass_index == pass_index:
                return stepper.record_array(trace, record)
        raise ValueError(f"no node {name!r} in pass {pass_index} of {trace_id}")

    def attention(
        self, trace_id: str, layer: int, head: int = 0, pass_index: int = 0
    ) -> Any:
        """One head's query x key matrix at `layer`, cropped to the KV in use."""
        from backend.modules.llamacpp import stepper

        trace = _load(trace_id)
        for record in trace.records:
            if (
                record.pass_index == pass_index
                and record.layer == layer
                and record.name.startswith("kq_soft_max")
            ):
                array = stepper.record_array(trace, record)[0, head]
                used = stepper.kv_in_use(trace, pass_index, array.shape[1])
                return array[:, :used]
        raise ValueError(
            f"no attention at layer {layer} — trace with attention capture on"
        )

    def delta(self, trace_id: str, layer: int, pass_index: int = 0) -> dict[str, Any]:
        """What block `layer` wrote into the residual stream, per token."""
        from backend.modules.llamacpp import stepper

        return stepper.residual_delta(_load(trace_id), layer, pass_index)

    def experts(self, trace_id: str, pass_index: int = 0) -> dict[str, Any]:
        """Mixture-of-experts routing (`moe: False` on a dense model)."""
        from backend.modules.llamacpp import stepper

        return stepper.experts(_load(trace_id), pass_index)

    def focus(
        self,
        *,
        trace_id: str = "",
        layer: int | None = None,
        position: int | None = None,
        token_id: int | None = None,
        stage: str | None = None,
        step: int | None = None,
    ) -> dict[str, Any]:
        """Point the user's panes at part of the model (the model-locus bus).
        `stage` (`attention`, `ffn`, …) and `step` (a record index) move the
        stepper's cursor, which the model explorer follows."""
        from backend.modules.llamacpp import locus as lens_locus
        from backend.modules.llamacpp import traces

        trace = traces.load(trace_id) if trace_id else None
        return lens_locus.set_locus(
            {
                "traceId": trace_id or None,
                "modelSha": (
                    str(trace.manifest.get("modelSha") or "") if trace else None
                ),
                "layer": layer,
                "position": position,
                "tokenId": token_id,
                "stage": stage,
                "step": step,
            },
            source="dash",
        )

    def locus(self) -> dict[str, Any]:
        """The model locus most recently set from this side."""
        from backend.modules.llamacpp import locus as lens_locus

        return lens_locus.current_locus()


def _load(trace_id: str) -> Any:
    from backend.modules.llamacpp import traces

    trace = traces.load(trace_id)
    if trace is None:
        raise ValueError(f"no trace {trace_id}")
    return trace


def register_dash_facade() -> None:
    from backend.sdk.registry import registry

    registry.dash_facades["lens"] = _Lens
