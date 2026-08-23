"""Starting points: the graphs the designer offers instead of an empty canvas.

These are also the codegen fixtures. That is deliberate — a template the user opens
on day one and a golden file the test suite compares against should not be allowed
to drift apart, and the cheapest way to guarantee they don't is for them to be the
same object.
"""

from __future__ import annotations

from backend.modules.interpretability.graph.models import (
    DesignGraph,
    GraphEdge,
    GraphNode,
    SubGraph,
)


def _n(node_id: str, node_type: str, **params: object) -> GraphNode:
    return GraphNode(id=node_id, type=node_type, params=params)


def _e(source: str, target: str, handle: str = "in") -> GraphEdge:
    return GraphEdge(
        id=f"{source}->{target}:{handle}",
        source=source,
        target=target,
        targetHandle=handle,
    )


def decoder_block(
    gid: str, name: str, ffn_type: str, ffn_params: dict[str, object]
) -> SubGraph:
    """Pre-norm decoder block: two residual branches, each around a normed sublayer.

    Both `op.add` nodes take the block's own input on one side — that wire *is* the
    residual connection, which is exactly why it is drawn rather than implied.
    """
    return SubGraph(
        id=gid,
        name=name,
        nodes=[
            _n(f"{gid}_gin", "io.group_input"),
            _n(f"{gid}_n1", "norm.rms"),
            _n(f"{gid}_attn", "attn.mha"),
            _n(f"{gid}_res1", "op.add"),
            _n(f"{gid}_n2", "norm.rms"),
            _n(f"{gid}_ffn", ffn_type, **ffn_params),
            _n(f"{gid}_res2", "op.add"),
            _n(f"{gid}_gout", "io.group_output"),
        ],
        edges=[
            _e(f"{gid}_gin", f"{gid}_n1"),
            _e(f"{gid}_n1", f"{gid}_attn"),
            _e(f"{gid}_gin", f"{gid}_res1"),
            _e(f"{gid}_attn", f"{gid}_res1"),
            _e(f"{gid}_res1", f"{gid}_n2"),
            _e(f"{gid}_n2", f"{gid}_ffn"),
            _e(f"{gid}_res1", f"{gid}_res2"),
            _e(f"{gid}_ffn", f"{gid}_res2"),
            _e(f"{gid}_res2", f"{gid}_gout"),
        ],
    )


def stack(name: str, config: dict[str, object], block: SubGraph) -> DesignGraph:
    return DesignGraph(
        name=name,
        config=config,  # type: ignore[arg-type]
        groups=[block],
        nodes=[
            _n("input", "io.input"),
            _n("embed", "embed.token"),
            _n("blocks", "group", group=block.id, count="$n_layers"),
            _n("final_norm", "norm.rms"),
            _n("head", "ffn.linear", dim="$d_model", out_features="$vocab_size"),
            _n("output", "io.output"),
        ],
        edges=[
            _e("input", "embed"),
            _e("embed", "blocks"),
            _e("blocks", "final_norm"),
            _e("final_norm", "head"),
            _e("head", "output"),
        ],
    )


def llama_small() -> DesignGraph:
    """A modern decoder: RMSNorm, grouped-query attention, SwiGLU, RoPE.

    Small on purpose — every dimension here is a number you can change in the
    inspector and immediately run, which is the point of the pane.
    """
    return stack(
        "TinyLlama",
        {
            "vocab_size": 32000,
            "d_model": 512,
            "n_heads": 8,
            "n_kv_heads": 2,
            "ffn_hidden": 1376,
            "n_layers": 8,
        },
        decoder_block("blk", "DecoderBlock", "ffn.swiglu", {}),
    )


def gpt_small() -> DesignGraph:
    """The older shape: LayerNorm, full multi-head attention, a dense GELU MLP.

    Kept beside `llama_small` because the diff between the two graphs is a compact
    statement of what actually changed in five years of decoder design.
    """
    graph = stack(
        "NanoGPT",
        {
            "vocab_size": 50257,
            "d_model": 384,
            "n_heads": 6,
            "n_kv_heads": 6,
            "ffn_hidden": 1536,
            "n_layers": 6,
            "max_seq": 1024,
        },
        decoder_block("blk", "TransformerBlock", "ffn.mlp", {"activation": "gelu"}),
    )
    block = graph.groups[0]
    for node in block.nodes:
        if node.type == "norm.rms":
            node.type = "norm.layer"
        if node.type == "attn.mha":
            node.params["rope"] = False
    for node in graph.nodes:
        if node.type == "norm.rms":
            node.type = "norm.layer"
    # Absolute positions, since attention is no longer carrying them.
    graph.nodes.insert(2, _n("pos", "embed.learned_positional", max_seq="$max_seq"))
    graph.edges = [e for e in graph.edges if e.id != "embed->blocks:in"]
    graph.edges += [_e("embed", "pos"), _e("pos", "blocks")]
    return graph


def moe_small() -> DesignGraph:
    """A sparse decoder: the FFN branch is a router over eight gated experts."""
    return stack(
        "TinyMoE",
        {
            "vocab_size": 32000,
            "d_model": 512,
            "n_heads": 8,
            "n_kv_heads": 2,
            "ffn_hidden": 1024,
            "n_layers": 4,
        },
        decoder_block("blk", "MoEBlock", "ffn.moe", {"experts": 8, "top_k": 2}),
    )


#: id → (label, one-line description, builder). The palette's "New from template".
TEMPLATES = {
    "llama": (
        "Modern decoder",
        "RMSNorm · grouped-query attention · SwiGLU · RoPE",
        llama_small,
    ),
    "gpt": (
        "Classic decoder",
        "LayerNorm · multi-head attention · dense GELU MLP",
        gpt_small,
    ),
    "moe": ("Sparse decoder", "Eight gated experts, two active per token", moe_small),
}


def template(name: str) -> DesignGraph | None:
    entry = TEMPLATES.get(name)
    return entry[2]() if entry else None
