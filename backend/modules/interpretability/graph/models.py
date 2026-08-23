"""The design graph IR: what a model *you are authoring* is, before it is code.

Deliberately **not** `ModelArchitecture` (../models.py) and deliberately not
`ModelGraph` (`packages/core/src/modules/training/client.ts`). Those two describe a
model that already exists — one normalized out of GGUF metadata, one traced out of a
running `nn.Module`. This one is a *design*: the thing the node editor edits and the
thing `codegen.py` turns into a `nn.Module` subclass.

Two rules the shape follows, and both are load-bearing:

- **No cosmetics in here.** Positions, frames, collapse state and labels live in a
  separate layout sidecar. The `.py` file codegen emits is the source of truth for
  structure, and a `.py` cannot carry x/y coordinates; keeping them out of the IR is
  what makes round-tripping through source lossless in the direction that matters.
- **A param is a literal or a reference, never ambiguously both.** `"$d_model"` reads
  the graph's config; anything else is the value itself. Guessing which a bare string
  meant is exactly the silent reinterpretation the pane exists to refuse.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: One dimension of a tensor shape: a concrete size, or a symbol like "B" / "T" that
#: is only known at run time. A symbol is never quietly assumed to equal anything.
Dim = int | str
Shape = list[Dim]

SocketType = Literal["tensor", "int", "float", "bool"]


class GraphNode(BaseModel):
    """One operator instance on the canvas."""

    id: str
    #: A key into the `spec.py` catalog — "norm.rms", "attn.mha", "group", …
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    #: Blender's node mute (`M`): the node emits nothing and its first input is
    #: threaded straight to its output. Here that is ablation — mute a block,
    #: regenerate, compare — so it is part of the design, not a view state.
    muted: bool = False
    #: User-supplied override for the generated attribute name (`self.<name>`).
    #: Empty means codegen derives one.
    name: str = ""


class GraphEdge(BaseModel):
    """A link. Handle names are socket names from the node's spec."""

    id: str = ""
    source: str
    sourceHandle: str = "out"
    target: str
    targetHandle: str = "in"


class SubGraph(BaseModel):
    """A node group — which is to say, one generated `nn.Module` subclass.

    This is the whole reason Blender's metaphor fits a neural network: a group is
    already a reusable, parametrizable unit with declared inputs and outputs, and so
    is a `nn.Module`. Nesting is allowed; recursion is not (`codegen` raises).
    """

    id: str
    #: Becomes the generated class name, so it is PascalCased on the way out.
    name: str = "Block"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class DesignGraph(BaseModel):
    """A complete design: the root graph, its groups, and its hyperparameters.

    `config` entries become the generated root class's `__init__` keyword arguments,
    which is what lets one graph describe a family of models (`d_model=2048` today,
    `4096` tomorrow) instead of a single frozen one. Node params reference them as
    `"$d_model"`.
    """

    name: str = "MyModel"
    config: dict[str, int | float | bool | str] = Field(default_factory=dict)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    groups: list[SubGraph] = Field(default_factory=list)

    def group(self, gid: str) -> SubGraph | None:
        return next((g for g in self.groups if g.id == gid), None)


class ShapeIssue(BaseModel):
    """One reason the graph would not run, located precisely enough to draw.

    `nodeId`/`handle` are what turn a message into a red socket. A problem we can
    only attribute to the graph as a whole leaves them empty rather than blaming an
    arbitrary node.
    """

    nodeId: str = ""
    handle: str = ""
    edgeId: str = ""
    severity: Literal["error", "warning"] = "error"
    message: str


class ShapeReport(BaseModel):
    """The result of pure-Python shape inference — tier 1 of two.

    This is *our* arithmetic, not torch's, and the pane labels it as such. Tier 2
    (`probe.py`) instantiates the module in a project venv and asks torch. When the
    two disagree, tier 1 is wrong.
    """

    ok: bool = True
    #: Node id → its output socket shapes, for the wire labels.
    shapes: dict[str, dict[str, Shape]] = Field(default_factory=dict)
    #: Node id → parameter count, for the cost overlay. Estimated until a probe runs.
    params: dict[str, int] = Field(default_factory=dict)
    totalParams: int = 0
    issues: list[ShapeIssue] = Field(default_factory=list)


class CodeResult(BaseModel):
    """Generated source, plus the map back to the nodes that produced it.

    `markers` is the round-trip hinge: every emitted line carries
    `# horrible:node=<id>`, so re-parsing the file recovers node identity — and with
    it the layout sidecar — instead of generating fresh ids and scattering the
    user's canvas on every save.
    """

    source: str
    #: 1-based line number → node id.
    markers: dict[int, str] = Field(default_factory=dict)
    #: Generated class → its `self.<attr>` → the node that emitted it. This is the
    #: other half of the identity map: `markers` says which node wrote a *line*,
    #: this says which node became a *runtime module*, which is the only way a
    #: measured `decoderblocks_1.0.norm_1` finds its way back to a box on the canvas.
    attrs: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: Generated class → its `self.<attr>` → the class that attribute instantiates,
    #: for the attributes that hold a group. Walking a runtime path needs it: without
    #: knowing `decoderblocks_1` holds a `DecoderBlock`, the `norm_1` under it cannot
    #: be looked up in any table.
    attrClasses: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: The class the model itself is, where a runtime path starts.
    rootClass: str = ""
    error: str | None = None
