"""The node catalog: the vocabulary the canvas offers and the code each node emits.

One entry per node type, and each entry carries everything the rest of the module
needs to know about it — its sockets, its editable params, how its output shape
follows from its inputs, how many parameters it holds, and the two lines of Python
it becomes. Adding a node type is adding an entry here and nothing else.

Three conventions worth knowing before reading the table:

- **A node declares its width.** Anything that owns weights takes a `dim` param that
  defaults to `"$d_model"` — a reference to the graph's config. That keeps the
  generated class parametric (`RMSNorm(d_model)`, not `RMSNorm(2048)`) and it makes a
  width mismatch a *stated* disagreement between the node and the tensor reaching it,
  which shape inference can point at, rather than something inferred silently.
- **A residual connection is an `op.add` node.** It is not a flag on a block and not
  an implicit convention: it is a wire you can see, reroute, mute, or get wrong.
- **Nothing converts implicitly.** Blender lets a float become a colour; a tensor
  never becomes a differently-shaped tensor here without an explicit `op.reshape`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.modules.interpretability.graph.models import (
    Dim,
    GraphNode,
    Shape,
    SocketType,
)


class ShapeError(ValueError):
    """A node cannot produce an output from the inputs it was given.

    Raised by a `shape_fn` and caught by `shapes.infer`, which turns it into a
    located `ShapeIssue` rather than letting it escape as a 500.
    """


@dataclass(frozen=True)
class SocketDecl:
    name: str
    type: SocketType = "tensor"
    #: Blender's ellipsis socket: accepts any number of links. Only `op.add` and
    #: `op.concat` want it, and both fold their inputs in link order.
    multi: bool = False
    label: str = ""


@dataclass(frozen=True)
class ParamDecl:
    """One editable property, in the `RecipeField` shape the training pane already
    renders — same type vocabulary, same `help`-is-mandatory discipline."""

    name: str
    label: str
    type: str  # int | float | bool | text | select
    default: Any
    help: str
    options: tuple[str, ...] = ()


@dataclass
class Ctx:
    """Resolution of `"$name"` param references against the graph's config."""

    config: dict[str, Any] = field(default_factory=dict)

    def value(self, node: GraphNode, name: str) -> Any:
        """The param's resolved value — the literal, or what the config says."""
        spec = SPECS[node.type]
        raw = node.params.get(name, _default(spec, name))
        if isinstance(raw, str) and raw.startswith("$"):
            key = raw[1:]
            if key not in self.config:
                raise ShapeError(
                    f"{node.type}.{name} references ${key}, which the model config does not define"
                )
            return self.config[key]
        return raw

    def code(self, node: GraphNode, name: str) -> str:
        """The param as it appears in generated source.

        A reference emits the *variable name*, so the generated class keeps its
        `__init__` keyword argument instead of freezing today's number into it.
        """
        spec = SPECS[node.type]
        raw = node.params.get(name, _default(spec, name))
        if isinstance(raw, str) and raw.startswith("$"):
            return raw[1:]
        return repr(raw)

    def refs(self, node: GraphNode) -> set[str]:
        """Config keys this node reads — what a group's `__init__` must accept."""
        spec = SPECS.get(node.type)
        if spec is None:
            return set()
        out: set[str] = set()
        for param in spec.params:
            raw = node.params.get(param.name, param.default)
            if isinstance(raw, str) and raw.startswith("$"):
                out.add(raw[1:])
        return out


#: `(node, input shapes by handle, ctx) -> the output shape`.
ShapeFn = Callable[[GraphNode, dict[str, Shape], Ctx], Shape]
#: `(node, input shapes by handle, ctx) -> learnable parameter count`.
ParamsFn = Callable[[GraphNode, dict[str, Shape], Ctx], int]
#: `(node, ctx) -> the constructor expression for `self.<attr>`, or None if the node
#: owns no submodule (a pure op like `op.add`).
InitFn = Callable[[GraphNode, Ctx], str | None]
#: `(node, `self.<attr>` or "", input expressions by handle, ctx) -> an expression`.
ForwardFn = Callable[[GraphNode, str, dict[str, str], Ctx], str]


@dataclass(frozen=True)
class NodeSpec:
    type: str
    label: str
    category: str
    inputs: tuple[SocketDecl, ...] = ()
    outputs: tuple[SocketDecl, ...] = (SocketDecl("out"),)
    params: tuple[ParamDecl, ...] = ()
    #: Stem for the generated attribute name (`norm` → `self.norm_1`). Empty when the
    #: node owns no submodule.
    attr: str = ""
    #: Emitted primitive classes this node's code needs.
    prims: tuple[str, ...] = ()
    doc: str = ""
    shape_fn: ShapeFn | None = None
    params_fn: ParamsFn | None = None
    init_fn: InitFn | None = None
    forward_fn: ForwardFn | None = None


SPECS: dict[str, NodeSpec] = {}


def _default(spec: NodeSpec, name: str) -> Any:
    for param in spec.params:
        if param.name == name:
            return param.default
    return None


def _register(spec: NodeSpec) -> NodeSpec:
    SPECS[spec.type] = spec
    return spec


def spec_for(node_type: str) -> NodeSpec | None:
    return SPECS.get(node_type)


# ── shape helpers ────────────────────────────────────────────────────────────────


def _same(_node: GraphNode, ins: dict[str, Shape], _ctx: Ctx) -> Shape:
    """Elementwise: the output is the input, unchanged."""
    return list(next(iter(ins.values())))


def _require_last(shape: Shape, want: Dim, node: GraphNode, handle: str = "in") -> None:
    """The incoming width must be the width the node says it has.

    Symbolic dims pass: `[B, T, "d_model"]` against 2048 is not a disagreement, it is
    an unknown, and reporting an unknown as an error would make every un-run graph
    look broken.
    """
    if not shape:
        raise ShapeError(f"{node.type} got a scalar on `{handle}`")
    got = shape[-1]
    if isinstance(got, int) and isinstance(want, int) and got != want:
        raise ShapeError(f"expected width {want}, got {got}")


def _dim_param(help_text: str = "Model width this node operates on.") -> ParamDecl:
    return ParamDecl("dim", "Width", "text", "$d_model", help_text)


# ── I/O ──────────────────────────────────────────────────────────────────────────

_register(
    NodeSpec(
        type="io.input",
        label="Input",
        category="io",
        outputs=(SocketDecl("out", label="ids"),),
        doc="Token ids entering the model: [batch, seq].",
        shape_fn=lambda n, ins, ctx: ["B", "T"],
    )
)

_register(
    NodeSpec(
        type="io.output",
        label="Output",
        category="io",
        inputs=(SocketDecl("in", label="logits"),),
        outputs=(),
        doc="What `forward` returns.",
        shape_fn=_same,
    )
)

_register(
    NodeSpec(
        type="io.group_input",
        label="Group input",
        category="io",
        outputs=(SocketDecl("out", label="x"),),
        doc="The tensor a node group is handed. Only meaningful inside a group.",
        shape_fn=lambda n, ins, ctx: ["B", "T", ctx.config.get("d_model", "d_model")],
    )
)

_register(
    NodeSpec(
        type="io.group_output",
        label="Group output",
        category="io",
        inputs=(SocketDecl("in", label="x"),),
        outputs=(),
        doc="What a node group returns.",
        shape_fn=_same,
    )
)


# ── embeddings ───────────────────────────────────────────────────────────────────


def _embed_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    shape = ins.get("in", [])
    if len(shape) != 2:
        raise ShapeError(f"token embedding wants [batch, seq], got {shape}")
    return [*shape, ctx.value(node, "dim")]


_register(
    NodeSpec(
        type="embed.token",
        label="Token embedding",
        category="embedding",
        inputs=(SocketDecl("in", label="ids"),),
        params=(
            ParamDecl(
                "vocab_size",
                "Vocabulary",
                "text",
                "$vocab_size",
                "Number of tokens in the vocabulary.",
            ),
            _dim_param("Embedding width — the model's residual stream width."),
        ),
        attr="embed",
        doc="Token ids to vectors. Usually the largest single tensor in a small model.",
        shape_fn=_embed_shape,
        params_fn=lambda n, ins, ctx: (
            int(ctx.value(n, "vocab_size")) * int(ctx.value(n, "dim"))
        ),
        init_fn=lambda n, ctx: (
            f"nn.Embedding({ctx.code(n, 'vocab_size')}, {ctx.code(n, 'dim')})"
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


def _pos_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    shape = list(ins["in"])
    _require_last(shape, ctx.value(node, "dim"), node)
    return shape


_register(
    NodeSpec(
        type="embed.learned_positional",
        label="Learned positional",
        category="embedding",
        inputs=(SocketDecl("in"),),
        params=(
            ParamDecl(
                "max_seq",
                "Max sequence",
                "int",
                2048,
                "Longest position this table can encode.",
            ),
            _dim_param(),
        ),
        attr="pos",
        doc="An absolute position table added to the residual stream. RoPE is a property of attention instead.",
        shape_fn=_pos_shape,
        params_fn=lambda n, ins, ctx: (
            int(ctx.value(n, "max_seq")) * int(ctx.value(n, "dim"))
        ),
        init_fn=lambda n, ctx: (
            f"nn.Embedding({ctx.code(n, 'max_seq')}, {ctx.code(n, 'dim')})"
        ),
        forward_fn=(
            lambda n, attr, ins, ctx: (
                f"{ins['in']} + {attr}(torch.arange({ins['in']}.shape[1], device={ins['in']}.device))"
            )
        ),
    )
)


# ── normalisation ────────────────────────────────────────────────────────────────

_register(
    NodeSpec(
        type="norm.rms",
        label="RMSNorm",
        category="norm",
        inputs=(SocketDecl("in"),),
        params=(
            _dim_param(),
            ParamDecl(
                "eps",
                "Epsilon",
                "float",
                1e-6,
                "Added under the square root for numerical stability.",
            ),
        ),
        attr="norm",
        prims=("RMSNorm",),
        doc="LayerNorm without the mean subtraction — what most current LLMs use.",
        shape_fn=_pos_shape,
        params_fn=lambda n, ins, ctx: int(ctx.value(n, "dim")),
        init_fn=lambda n, ctx: (
            f"RMSNorm({ctx.code(n, 'dim')}, eps={ctx.code(n, 'eps')})"
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)

_register(
    NodeSpec(
        type="norm.layer",
        label="LayerNorm",
        category="norm",
        inputs=(SocketDecl("in"),),
        params=(
            _dim_param(),
            ParamDecl(
                "eps",
                "Epsilon",
                "float",
                1e-5,
                "Added to the variance for numerical stability.",
            ),
            ParamDecl(
                "bias", "Bias", "bool", True, "Learn a shift as well as a scale."
            ),
        ),
        attr="norm",
        doc="Mean-and-variance normalisation with a learned affine.",
        shape_fn=_pos_shape,
        params_fn=lambda n, ins, ctx: (
            int(ctx.value(n, "dim")) * (2 if ctx.value(n, "bias") else 1)
        ),
        init_fn=(
            lambda n, ctx: (
                f"nn.LayerNorm({ctx.code(n, 'dim')}, eps={ctx.code(n, 'eps')}, bias={ctx.code(n, 'bias')})"
            )
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


# ── attention ────────────────────────────────────────────────────────────────────


def _attn_heads(node: GraphNode, ctx: Ctx) -> tuple[int, int, int]:
    dim = int(ctx.value(node, "dim"))
    heads = int(ctx.value(node, "heads"))
    kv_heads = int(ctx.value(node, "kv_heads")) or heads
    head_dim = int(ctx.value(node, "head_dim")) or (dim // heads if heads else 0)
    if heads <= 0:
        raise ShapeError("attention needs at least one head")
    if kv_heads <= 0 or heads % kv_heads:
        raise ShapeError(
            f"heads ({heads}) must be a whole multiple of kv_heads ({kv_heads})"
        )
    return heads, kv_heads, head_dim


def _attn_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    shape = list(ins["in"])
    _require_last(shape, ctx.value(node, "dim"), node)
    _attn_heads(node, ctx)
    return shape


def _attn_params(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> int:
    dim = int(ctx.value(node, "dim"))
    heads, kv_heads, head_dim = _attn_heads(node, ctx)
    bias = 1 if ctx.value(node, "bias") else 0
    q = dim * heads * head_dim + bias * heads * head_dim
    kv = 2 * (dim * kv_heads * head_dim + bias * kv_heads * head_dim)
    o = heads * head_dim * dim + bias * dim
    return q + kv + o


_register(
    NodeSpec(
        type="attn.mha",
        label="Attention",
        category="attention",
        inputs=(SocketDecl("in"),),
        params=(
            _dim_param(),
            ParamDecl(
                "heads", "Query heads", "text", "$n_heads", "Number of query heads."
            ),
            ParamDecl(
                "kv_heads",
                "KV heads",
                "text",
                "$n_kv_heads",
                "Key/value heads. Equal to query heads is MHA, 1 is MQA, in between is GQA — "
                "and this ratio is the single biggest driver of KV-cache size.",
            ),
            ParamDecl(
                "head_dim",
                "Head width",
                "int",
                0,
                "Per-head width. 0 derives it as width / heads.",
            ),
            ParamDecl(
                "causal",
                "Causal mask",
                "bool",
                True,
                "A token may only attend to itself and its past.",
            ),
            ParamDecl(
                "rope", "Rotary embedding", "bool", True, "Apply RoPE to q and k."
            ),
            ParamDecl(
                "dropout", "Dropout", "float", 0.0, "Attention dropout, training only."
            ),
            ParamDecl(
                "bias", "Bias", "bool", False, "Bias terms on the four projections."
            ),
        ),
        attr="attn",
        prims=("MultiHeadAttention",),
        doc="Self-attention. MHA, GQA and MQA are the same node, distinguished by the head counts.",
        shape_fn=_attn_shape,
        params_fn=_attn_params,
        init_fn=lambda n, ctx: (
            "MultiHeadAttention("
            f"{ctx.code(n, 'dim')}, heads={ctx.code(n, 'heads')}, kv_heads={ctx.code(n, 'kv_heads')}, "
            f"head_dim={ctx.code(n, 'head_dim')}, causal={ctx.code(n, 'causal')}, rope={ctx.code(n, 'rope')}, "
            f"dropout={ctx.code(n, 'dropout')}, bias={ctx.code(n, 'bias')})"
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


# ── feed-forward ─────────────────────────────────────────────────────────────────


def _linear_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    shape = list(ins["in"])
    _require_last(shape, ctx.value(node, "dim"), node)
    return [*shape[:-1], ctx.value(node, "out_features")]


_register(
    NodeSpec(
        type="ffn.linear",
        label="Linear",
        category="ffn",
        inputs=(SocketDecl("in"),),
        params=(
            _dim_param("Input width."),
            ParamDecl(
                "out_features",
                "Output width",
                "text",
                "$vocab_size",
                "Width of the produced tensor.",
            ),
            ParamDecl("bias", "Bias", "bool", False, "Learn an additive term."),
        ),
        attr="proj",
        doc="A plain projection. The LM head is one of these, from width to vocabulary.",
        shape_fn=_linear_shape,
        params_fn=lambda n, ins, ctx: (
            int(ctx.value(n, "dim")) * int(ctx.value(n, "out_features"))
            + (int(ctx.value(n, "out_features")) if ctx.value(n, "bias") else 0)
        ),
        init_fn=(
            lambda n, ctx: (
                f"nn.Linear({ctx.code(n, 'dim')}, {ctx.code(n, 'out_features')}, bias={ctx.code(n, 'bias')})"
            )
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


def _gated_params(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> int:
    dim, hidden = int(ctx.value(node, "dim")), int(ctx.value(node, "hidden"))
    bias = 1 if ctx.value(node, "bias") else 0
    return 3 * dim * hidden + bias * (2 * hidden + dim)


for _type, _label, _prim, _doc in (
    (
        "ffn.swiglu",
        "SwiGLU",
        "SwiGLU",
        "The gated FFN most current LLMs use: three matrices, not two.",
    ),
    ("ffn.geglu", "GeGLU", "GeGLU", "SwiGLU with a GELU gate."),
):
    _register(
        NodeSpec(
            type=_type,
            label=_label,
            category="ffn",
            inputs=(SocketDecl("in"),),
            params=(
                _dim_param(),
                ParamDecl(
                    "hidden",
                    "Hidden width",
                    "text",
                    "$ffn_hidden",
                    "Width of the up-projection.",
                ),
                ParamDecl(
                    "bias",
                    "Bias",
                    "bool",
                    False,
                    "Bias terms on the three projections.",
                ),
            ),
            attr="ffn",
            prims=(_prim,),
            doc=_doc,
            shape_fn=_pos_shape,
            params_fn=_gated_params,
            init_fn=(
                lambda n, ctx, _p=_prim: (
                    f"{_p}({ctx.code(n, 'dim')}, {ctx.code(n, 'hidden')}, bias={ctx.code(n, 'bias')})"
                )
            ),
            forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
        )
    )

_register(
    NodeSpec(
        type="ffn.mlp",
        label="MLP",
        category="ffn",
        inputs=(SocketDecl("in"),),
        params=(
            _dim_param(),
            ParamDecl(
                "hidden",
                "Hidden width",
                "text",
                "$ffn_hidden",
                "Width of the up-projection.",
            ),
            ParamDecl(
                "activation",
                "Activation",
                "select",
                "gelu",
                "Nonlinearity between the two matrices.",
                ("gelu", "relu", "silu", "tanh"),
            ),
            ParamDecl("bias", "Bias", "bool", True, "Bias terms on both projections."),
        ),
        attr="ffn",
        prims=("MLP",),
        doc="The dense feed-forward network: two matrices with a nonlinearity between them.",
        shape_fn=_pos_shape,
        params_fn=lambda n, ins, ctx: (
            2 * int(ctx.value(n, "dim")) * int(ctx.value(n, "hidden"))
            + (
                (int(ctx.value(n, "hidden")) + int(ctx.value(n, "dim")))
                if ctx.value(n, "bias")
                else 0
            )
        ),
        init_fn=lambda n, ctx: (
            f"MLP({ctx.code(n, 'dim')}, {ctx.code(n, 'hidden')}, "
            f"activation={ctx.code(n, 'activation')}, bias={ctx.code(n, 'bias')})"
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)

_register(
    NodeSpec(
        type="ffn.moe",
        label="Mixture of experts",
        category="ffn",
        inputs=(SocketDecl("in"),),
        params=(
            _dim_param(),
            ParamDecl(
                "hidden",
                "Expert width",
                "text",
                "$ffn_hidden",
                "Up-projection width of one expert.",
            ),
            ParamDecl(
                "experts",
                "Experts",
                "int",
                8,
                "How many experts the router chooses between.",
            ),
            ParamDecl(
                "top_k",
                "Experts per token",
                "int",
                2,
                "How many experts each token actually passes through.",
            ),
            ParamDecl("bias", "Bias", "bool", False, "Bias terms inside each expert."),
        ),
        attr="moe",
        prims=("MoE",),
        doc=(
            "A router and N gated experts. Total parameters are the sum of every expert; "
            "active parameters are only top-k of them, which is the whole point."
        ),
        shape_fn=_pos_shape,
        params_fn=lambda n, ins, ctx: (
            int(ctx.value(n, "experts")) * _gated_params(n, ins, ctx)
            + int(ctx.value(n, "dim")) * int(ctx.value(n, "experts"))
        ),
        init_fn=lambda n, ctx: (
            f"MoE({ctx.code(n, 'dim')}, {ctx.code(n, 'hidden')}, experts={ctx.code(n, 'experts')}, "
            f"top_k={ctx.code(n, 'top_k')}, bias={ctx.code(n, 'bias')})"
        ),
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


# ── activations and elementwise ops ──────────────────────────────────────────────

for _type, _label, _fn in (
    ("act.silu", "SiLU", "F.silu"),
    ("act.gelu", "GELU", "F.gelu"),
    ("act.relu", "ReLU", "F.relu"),
    ("act.tanh", "Tanh", "torch.tanh"),
):
    _register(
        NodeSpec(
            type=_type,
            label=_label,
            category="activation",
            inputs=(SocketDecl("in"),),
            doc=f"Elementwise {_label}.",
            shape_fn=_same,
            forward_fn=lambda n, attr, ins, ctx, _f=_fn: f"{_f}({ins['in']})",
        )
    )


def _fold_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    shapes = [s for s in ins.values() if s]
    if len(shapes) < 2:
        raise ShapeError(f"{SPECS[node.type].label} needs at least two inputs")
    first = shapes[0]
    for other in shapes[1:]:
        if len(other) != len(first):
            raise ShapeError(f"cannot combine {first} with {other} — different rank")
        for a, b in zip(first, other):
            if isinstance(a, int) and isinstance(b, int) and a != b:
                raise ShapeError(f"cannot combine {first} with {other}")
    return list(first)


_register(
    NodeSpec(
        type="op.add",
        label="Add",
        category="op",
        inputs=(SocketDecl("in", multi=True),),
        doc=(
            "Elementwise sum — and therefore the residual connection. Wiring a block's "
            "input into one side of this is what makes it a residual block; there is no "
            "hidden flag that does it for you."
        ),
        shape_fn=_fold_shape,
        forward_fn=lambda n, attr, ins, ctx: " + ".join(ins[k] for k in sorted(ins)),
    )
)

_register(
    NodeSpec(
        type="op.mul",
        label="Multiply",
        category="op",
        inputs=(SocketDecl("in", multi=True),),
        doc="Elementwise product — gating.",
        shape_fn=_fold_shape,
        forward_fn=lambda n, attr, ins, ctx: " * ".join(ins[k] for k in sorted(ins)),
    )
)

_register(
    NodeSpec(
        type="op.scale",
        label="Scale",
        category="op",
        inputs=(SocketDecl("in"),),
        params=(
            ParamDecl(
                "factor",
                "Factor",
                "float",
                1.0,
                "Constant the tensor is multiplied by.",
            ),
        ),
        doc="Multiply by a constant.",
        shape_fn=_same,
        forward_fn=lambda n, attr, ins, ctx: f"{ins['in']} * {ctx.code(n, 'factor')}",
    )
)

_register(
    NodeSpec(
        type="op.dropout",
        label="Dropout",
        category="op",
        inputs=(SocketDecl("in"),),
        params=(
            ParamDecl(
                "p",
                "Probability",
                "float",
                0.1,
                "Fraction of activations zeroed during training.",
            ),
        ),
        attr="drop",
        doc="Zero a fraction of activations while training.",
        shape_fn=_same,
        init_fn=lambda n, ctx: f"nn.Dropout({ctx.code(n, 'p')})",
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


def _concat_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    shapes = [s for s in ins.values() if s]
    if len(shapes) < 2:
        raise ShapeError("concat needs at least two inputs")
    axis = int(ctx.value(node, "dim_index"))
    out = list(shapes[0])
    total: Dim = 0
    for shape in shapes:
        if len(shape) != len(out):
            raise ShapeError(f"cannot concatenate {out} with {shape} — different rank")
        size = shape[axis]
        if isinstance(total, int) and isinstance(size, int):
            total += size
        else:
            total = "?"
    out[axis] = total
    return out


_register(
    NodeSpec(
        type="op.concat",
        label="Concatenate",
        category="op",
        inputs=(SocketDecl("in", multi=True),),
        params=(
            ParamDecl(
                "dim_index",
                "Axis",
                "int",
                -1,
                "Axis to join along. -1 is the feature axis.",
            ),
        ),
        doc="Join tensors along one axis.",
        shape_fn=_concat_shape,
        forward_fn=(
            lambda n, attr, ins, ctx: (
                f"torch.cat([{', '.join(ins[k] for k in sorted(ins))}], dim={ctx.code(n, 'dim_index')})"
            )
        ),
    )
)


def _reshape_shape(node: GraphNode, ins: dict[str, Shape], ctx: Ctx) -> Shape:
    raw = str(ctx.value(node, "shape")).strip()
    if not raw:
        raise ShapeError("reshape has no target shape")
    out: Shape = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ShapeError(f"reshape target {raw!r} has an empty axis")
        out.append(int(part) if part.lstrip("-").isdigit() else part)
    return out


_register(
    NodeSpec(
        type="op.reshape",
        label="Reshape",
        category="op",
        inputs=(SocketDecl("in"),),
        params=(
            ParamDecl(
                "shape",
                "Target shape",
                "text",
                "B, T, -1",
                "Comma-separated axes. Names resolve against the incoming tensor (B, T); -1 infers.",
            ),
        ),
        doc=(
            "The only way a tensor changes shape here. Nothing converts implicitly — a "
            "mismatch is a red wire you fix with one of these, not something that happens quietly."
        ),
        shape_fn=_reshape_shape,
        forward_fn=lambda n, attr, ins, ctx: _reshape_code(n, ins["in"], ctx),
    )
)


def _reshape_code(node: GraphNode, value: str, ctx: Ctx) -> str:
    """`B, T, -1` → `x.reshape(x.shape[0], x.shape[1], -1)`.

    Named axes resolve positionally against the incoming tensor, so the generated
    call stays correct for any batch size rather than baking in the one we inferred.
    """
    axes: list[str] = []
    for index, part in enumerate(str(ctx.value(node, "shape")).split(",")):
        part = part.strip()
        if part.lstrip("-").isdigit():
            axes.append(part)
        elif part == "B":
            axes.append(f"{value}.shape[0]")
        elif part == "T":
            axes.append(f"{value}.shape[1]")
        else:
            axes.append(f"{value}.shape[{index}]")
    return f"{value}.reshape({', '.join(axes)})"


# ── structure ────────────────────────────────────────────────────────────────────

_register(
    NodeSpec(
        type="struct.reroute",
        label="Reroute",
        category="structure",
        inputs=(SocketDecl("in"),),
        doc="A bend in a wire. Purely cosmetic — codegen walks straight through it.",
        shape_fn=_same,
        forward_fn=lambda n, attr, ins, ctx: ins["in"],
    )
)

_register(
    NodeSpec(
        type="group",
        label="Group",
        category="structure",
        inputs=(SocketDecl("in"),),
        params=(
            ParamDecl(
                "group", "Group", "text", "", "Id of the subgraph this instance runs."
            ),
            ParamDecl(
                "count",
                "Repeat",
                "int",
                1,
                "How many times to stack it. Above 1 this emits a ModuleList and a loop — "
                "the ×N the model explorer already draws instead of forty identical rectangles.",
            ),
        ),
        doc="One generated nn.Module subclass, instantiated here — optionally stacked N deep.",
        shape_fn=None,  # resolved by shapes.infer, which can descend into the subgraph
        forward_fn=None,
    )
)

_register(
    NodeSpec(
        type="custom.module",
        label="Custom module",
        category="structure",
        inputs=(SocketDecl("in"),),
        params=(
            ParamDecl(
                "class_name",
                "Class name",
                "text",
                "Custom",
                "Name of the class defined below.",
            ),
            ParamDecl(
                "code", "Source", "text", "", "A verbatim nn.Module class definition."
            ),
            ParamDecl(
                "args",
                "Constructor arguments",
                "text",
                "",
                "Emitted inside the constructor call.",
            ),
            ParamDecl(
                "out_shape",
                "Output shape",
                "text",
                "",
                "Comma-separated axes. Empty means unchanged.",
            ),
        ),
        attr="custom",
        doc=(
            "Your own code, verbatim. This is also where the round-trip parser puts source "
            "it cannot map onto a node — preserved and labelled opaque, never silently dropped."
        ),
        shape_fn=lambda n, ins, ctx: (
            _reshape_shape(n, ins, ctx)
            if str(ctx.value(n, "out_shape")).strip()
            else _same(n, ins, ctx)
        ),
        init_fn=lambda n, ctx: f"{ctx.value(n, 'class_name')}({ctx.value(n, 'args')})",
        forward_fn=lambda n, attr, ins, ctx: f"{attr}({ins['in']})",
    )
)


def catalog() -> list[dict[str, Any]]:
    """The catalog as the palette consumes it — everything but the callables."""
    return [
        {
            "type": spec.type,
            "label": spec.label,
            "category": spec.category,
            "doc": spec.doc,
            "inputs": [
                {
                    "name": s.name,
                    "type": s.type,
                    "multi": s.multi,
                    "label": s.label or s.name,
                }
                for s in spec.inputs
            ],
            "outputs": [
                {
                    "name": s.name,
                    "type": s.type,
                    "multi": s.multi,
                    "label": s.label or s.name,
                }
                for s in spec.outputs
            ],
            "params": [
                {
                    "name": p.name,
                    "label": p.label,
                    "type": p.type,
                    "default": p.default,
                    "help": p.help,
                    "options": list(p.options),
                }
                for p in spec.params
            ],
        }
        for spec in sorted(SPECS.values(), key=lambda s: (s.category, s.label))
    ]
