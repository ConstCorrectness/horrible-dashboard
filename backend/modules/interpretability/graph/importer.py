"""The bridge: a model you are *inspecting* becomes a model you can *edit*.

This is the reason Inspect and Design share one pane. Inspect answers "what is this
model"; Design answers "what if it were different"; and the obvious way to start the
second question is the answer to the first. Without this file the two modes are just
two things that happen to live near each other.

The whole difficulty is that `ModelArchitecture` is **deliberately full of holes**.
Every dimension on it is optional because the module refuses to invent one: a field
GGUF metadata did not state stays `None` and is simply not drawn. A graph, on the
other hand, has no holes — `attn.mha` needs a head count or it cannot compute a
shape, and codegen needs an FFN width or it cannot emit a `Linear`.

So the import is built on one rule, and the rule is the whole design:

- Facts without which there is **no graph at all** — width, depth, vocabulary, head
  count — are **required**. Missing any of them, this returns no graph and names
  what was missing. Inventing a hidden size would produce a plausible model of
  nothing.
- Facts that only shape the graph — is the FFN gated, is the norm RMS, is there
  RoPE — are **assumed when absent, and every assumption is reported**. That is not
  a hedge on the rule above: the output is an editable starting point, not a
  measurement, and it arrives with the list of everything we chose on your behalf.

The last line of defence is arithmetic. The result carries the parameter count the
metadata *stated* beside the count our own shape inference *derives* from the graph
we just built. If those two disagree, the import got something wrong — and saying so
is far more useful than a design that looks authoritative and is not.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.modules.interpretability.graph import codegen, examples, shapes
from backend.modules.interpretability.graph.models import DesignGraph
from backend.modules.interpretability.models import ModelArchitecture

#: Without these there is no graph, only a guess wearing one's clothes.
REQUIRED = (
    ("hiddenSize", "hidden size"),
    ("layers", "layer count"),
    ("vocabSize", "vocabulary size"),
)


class ImportResult(BaseModel):
    """A design derived from an inspected model, and everything qualifying it."""

    graph: DesignGraph | None = None
    #: The model this came from, for the notice the pane shows.
    model: str = ""
    source: str = ""
    #: Choices made because the metadata was silent. Every one of them is a thing
    #: the user may need to correct, so none of them are made quietly.
    assumed: list[str] = Field(default_factory=list)
    #: Required facts that were absent. Non-empty means `graph` is None.
    missing: list[str] = Field(default_factory=list)
    #: Caveats carried over from the inspection, plus ones the import adds.
    notes: list[str] = Field(default_factory=list)
    #: What the metadata said, and what our arithmetic makes of the graph we built.
    #: A disagreement is a bug in this file, not a rounding difference to shrug at.
    statedParams: int | None = None
    estimatedParams: int | None = None
    error: str | None = None


def from_architecture(arch: ModelArchitecture) -> ImportResult:
    """Turn an inspected model's normalized metadata into an editable design."""
    if arch.error:
        return ImportResult(error=arch.error, model=arch.model, source=arch.source)

    missing = [label for field, label in REQUIRED if getattr(arch, field) is None]
    attention = arch.attention
    if attention is None or attention.heads is None:
        missing.append("attention head count")
    if missing:
        return ImportResult(
            model=arch.model,
            source=arch.source,
            missing=missing,
            error=(
                "The metadata does not state "
                + ", ".join(missing)
                + ". Those decide the whole shape of the model, so importing would "
                "mean inventing them."
            ),
        )

    assert attention is not None and attention.heads is not None  # narrowed above
    assumed: list[str] = []
    notes = list(arch.notes)

    d_model = int(arch.hiddenSize or 0)
    heads = int(attention.heads)
    kv_heads = attention.kvHeads
    if kv_heads is None:
        kv_heads = heads
        assumed.append(
            "KV-head count absent — assumed full multi-head attention "
            f"({heads} KV heads). If this is a GQA model the KV cache is much smaller."
        )

    ffn_hidden, ffn_type, ffn_params = _ffn(arch, d_model, assumed)
    norm_type = _norm_type(arch, assumed)

    config: dict[str, object] = {
        "vocab_size": int(arch.vocabSize or 0),
        "d_model": d_model,
        "n_heads": heads,
        "n_kv_heads": int(kv_heads),
        "ffn_hidden": ffn_hidden,
        "n_layers": int(arch.layers or 0),
    }

    block = examples.decoder_block("blk", "DecoderBlock", ffn_type, ffn_params)
    graph = examples.stack(codegen.class_name(arch.model or "Imported"), config, block)

    _apply_norm(graph, norm_type)
    _apply_attention(graph, arch)
    _apply_rope(graph, arch, assumed)

    if attention.slidingWindow:
        notes.append(
            f"Attention uses a {attention.slidingWindow}-token sliding window, which "
            "no node models yet — the imported attention is full-context."
        )
    if attention.headDimDerived:
        notes.append(
            "Head width was derived as hidden ÷ heads rather than stated, so a model "
            "that decouples them (Gemma does) would import with the wrong width."
        )

    report = shapes.infer(graph)
    notes.extend(_reconcile(arch, config, report.totalParams))
    return ImportResult(
        graph=graph,
        model=arch.model,
        source=arch.source,
        assumed=assumed,
        notes=notes,
        statedParams=arch.parameterCount,
        estimatedParams=report.totalParams,
    )


def _reconcile(
    arch: ModelArchitecture, config: dict[str, object], estimated: int
) -> list[str]:
    """Explain the gap between the stated parameter count and our own.

    This is the import's own audit, and it earns its place: on Llama 3.2 3B the two
    differ by 12%, which looks like a broken import and is in fact one specific,
    knowable thing — the model ties its embedding and output head, and the graph
    gives the head its own matrix. Naming that is the difference between a number
    the reader can act on and one that just erodes their trust in the pane.

    A gap we *cannot* account for is reported as exactly that. Silence would let a
    genuinely wrong import pass for a right one.
    """
    stated = arch.parameterCount
    if not stated or not estimated:
        return []

    delta = estimated - stated
    if abs(delta) <= max(1, stated // 1000):
        return []

    head = int(config["vocab_size"]) * int(config["d_model"])  # type: ignore[call-overload]
    if head and abs(delta - head) <= max(1, head // 100):
        return [
            f"The design counts {estimated:,} parameters against the {stated:,} the "
            "metadata states. The difference is the output head: this model ties it "
            "to the embedding and the graph gives it its own matrix. Wire the head "
            "back to the embedding, or accept a model that is honestly larger."
        ]
    return [
        f"The design counts {estimated:,} parameters against the {stated:,} the "
        f"metadata states — a difference of {delta:+,} this import cannot account "
        "for. Treat the imported shape as a starting point rather than a faithful copy."
    ]


def _ffn(
    arch: ModelArchitecture, d_model: int, assumed: list[str]
) -> tuple[int, str, dict[str, object]]:
    """Which FFN the block gets, and how wide.

    `gated` genuinely changes the drawing — a gated FFN has three matrices where a
    dense one has two — so getting it wrong is a parameter count out by a third,
    which is exactly the kind of quiet wrongness the count comparison is there for.
    """
    ffn = arch.ffn
    hidden = ffn.intermediateSize if ffn else None
    if hidden is None:
        hidden = d_model * 4
        assumed.append(
            "Feed-forward width absent — assumed 4× the hidden size "
            f"({hidden}). This is the single biggest driver of parameter count."
        )

    moe = arch.moe
    if moe is not None and moe.experts:
        expert_hidden = moe.expertIntermediateSize or hidden
        if moe.expertIntermediateSize is None:
            assumed.append(
                "Per-expert width absent — assumed the dense feed-forward width."
            )
        top_k = moe.expertsPerToken or 2
        if not moe.expertsPerToken:
            assumed.append("Experts-per-token absent — assumed 2.")
        if moe.sharedExperts:
            assumed.append(
                f"{moe.sharedExperts} shared expert(s) are not modelled; the imported "
                "block routes every token through the top-k experts only."
            )
        return (
            expert_hidden,
            "ffn.moe",
            {"experts": int(moe.experts), "top_k": int(top_k)},
        )

    activation = (ffn.activation if ffn else None) or ""
    gated = ffn.gated if ffn else None
    if gated is None:
        gated = "glu" in activation.lower() or "silu" in activation.lower()
        assumed.append(
            "The metadata does not say whether the feed-forward network is gated — "
            f"assumed {'gated' if gated else 'dense'} from the activation "
            f"{activation or 'it did not state'}. A gated FFN has three matrices "
            "where a dense one has two, so this changes the parameter count."
        )
    if gated:
        kind = "ffn.geglu" if "gelu" in activation.lower() else "ffn.swiglu"
        return hidden, kind, {}
    return hidden, "ffn.mlp", {"activation": activation.lower() or "gelu"}


def _norm_type(arch: ModelArchitecture, assumed: list[str]) -> str:
    raw = (arch.normType or "").lower()
    if "rms" in raw:
        return "norm.rms"
    if "layer" in raw:
        return "norm.layer"
    assumed.append(
        "Normalisation type absent — assumed RMSNorm, which is what current decoders "
        "use. LayerNorm additionally has a bias and a mean subtraction."
    )
    return "norm.rms"


def _apply_norm(graph: DesignGraph, norm_type: str) -> None:
    if norm_type == "norm.rms":
        return
    for node in [*graph.nodes, *graph.groups[0].nodes]:
        if node.type == "norm.rms":
            node.type = norm_type


def _apply_attention(graph: DesignGraph, arch: ModelArchitecture) -> None:
    """Pin the head width only when the metadata decoupled it from hidden ÷ heads.

    Leaving `head_dim` at 0 lets the node derive it, which keeps the generated class
    parametric — a design where changing `d_model` also changes the head width is
    more useful than one where it silently does not.
    """
    attention = arch.attention
    if attention is None or attention.headDim is None or attention.headDimDerived:
        return
    heads = attention.heads or 0
    hidden = arch.hiddenSize or 0
    if heads and hidden and attention.headDim == hidden // heads:
        return
    for node in graph.groups[0].nodes:
        if node.type == "attn.mha":
            node.params["head_dim"] = int(attention.headDim)


def _apply_rope(
    graph: DesignGraph, arch: ModelArchitecture, assumed: list[str]
) -> None:
    """The imported attention always carries rotary embeddings.

    Absent RoPE parameters are ambiguous: they mean either "this model has none" or
    "the metadata omitted them". Assuming *none* would produce a decoder with no
    positional information whatsoever, which is certainly wrong, so the assumption
    goes the other way — and is reported, with what to do if it is wrong. Deciding
    between the two by reading tea leaves elsewhere in the metadata (a LayerNorm
    means GPT-2 means learned positions) would be a guess dressed as a deduction.
    """
    if arch.attention is None or arch.attention.ropeTheta is None:
        assumed.append(
            "No rotary-embedding parameters in the metadata — assumed RoPE anyway, "
            "since a decoder with no positional encoding at all would certainly be "
            "wrong. If this model uses learned positions instead, turn RoPE off on "
            "the attention node and add a Learned positional node after the embedding."
        )
    for node in graph.groups[0].nodes:
        if node.type == "attn.mha":
            node.params["rope"] = True
