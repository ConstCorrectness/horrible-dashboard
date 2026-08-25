"""Pydantic models for the interpretability module's API + `/ws` payloads.

The shapes are camelCase on the wire because the pane consumes them directly and
the rest of the app's `/ws` events are camelCase.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextBlock(BaseModel):
    """One addressable piece of the assembled prompt.

    `kind` is what makes the pane useful — the raw provider payload is an
    undifferentiated list of role/content dicts, so without labelling it you can't
    tell the system prompt from a tool guide from the focused editor buffer. The
    orchestrator knows which is which at assembly time; this preserves that.
    """

    kind: str  # system | skills | guides | history | editor | user | ...
    role: str
    label: str
    content: str
    tokens: int
    # True when `content` was clipped for transport. `tokens` still counts the FULL
    # text — a preview cap must never distort the numbers the pane reasons about.
    clipped: bool = False
    fullChars: int = 0


class ToolEntry(BaseModel):
    """One tool schema in the round's tool list, with its real context cost."""

    name: str
    group: str
    tokens: int


class RoundSnapshot(BaseModel):
    """The exact context handed to the model for one round of the tool loop."""

    round: int
    blocks: list[ContextBlock] = Field(default_factory=list)
    tools: list[ToolEntry] = Field(default_factory=list)
    messageTokens: int = 0
    toolTokens: int = 0
    totalTokens: int = 0
    # Progressive disclosure caps the tool list at TOOL_BUDGET and currently drops
    # the overflow with only a log line. `toolsSelected` is the pre-cap count, so
    # the pane can say "12 tools dropped" instead of the truncation being invisible.
    toolsSelected: int = 0
    toolBudget: int = 0
    toolsTruncated: bool = False
    activeGroups: list[str] = Field(default_factory=list)


class TurnSnapshot(BaseModel):
    """Every round of one agent turn, plus the sampling/window settings it ran under."""

    turnId: str
    agentId: str = "main"
    model: str = ""
    provider: str = ""
    startedAt: float = 0.0
    rounds: list[RoundSnapshot] = Field(default_factory=list)

    # ── Multi-agent shape ────────────────────────────────────────────────────
    # `main` can hand work to a scoped specialist via `agent.delegate`; the
    # sub-agent runs its own full loop on this same connection, so it is captured
    # like any other turn. `parentTurnId` is what turns those flat siblings back
    # into the handoff tree they actually are. None = a root turn the user started.
    parentTurnId: str | None = None
    agentName: str = ""
    # The agent's declared tool-group scope. None means unrestricted (only `main`
    # is), which is why it can't be conflated with "no groups".
    toolGroups: list[str] | None = None
    permissionMode: str | None = None

    # "local" — a real loop on this node, with rounds to inspect.
    # "peer"  — `agent.ask_peer` reached another user's node. Recorded so the tree
    #           has no unexplained gap, but it carries NO rounds: the peer's context
    #           lives on their machine, in their pane. See `peerId` / `sentPrompt`.
    kind: str = "local"
    peerId: str | None = None
    sentPrompt: str | None = None

    # False when counts can't be trusted as authoritative — either no tokenizer was
    # available (chars/4 estimates) or only a same-family stand-in was. The pane
    # renders this prominently; see tokenizer.py for why it can happen.
    exact: bool = True
    tokenizerRepo: str | None = None
    # How the tokenizer was chosen: "model" | "setting" | "family" | "none".
    # "family" means right family, possibly wrong generation — close, not correct.
    tokenizerSource: str = "none"

    # `requestedNumCtx` is what we ask for; `modelContextLength` is what the model
    # actually has. They differ more often than you'd expect, and the gap is the
    # difference between "my prompt fits" and "my prompt was silently truncated".
    requestedNumCtx: int | None = None
    modelContextLength: int | None = None
    temperature: float | None = None
    topP: float | None = None
    maxTokens: int | None = None


class TurnListResponse(BaseModel):
    turns: list[TurnSnapshot] = Field(default_factory=list)


class TurnSummary(BaseModel):
    """One stored turn without its context blocks — the shape `store.list_turns`
    returns, and the shape a listing is allowed to carry (no prompt text, no tool
    results; a caller that wants content asks for one turn by id).

    Deliberately *not* a `TurnSnapshot` with an empty `rounds` list: `rounds` here
    is a count, and a summary wearing a snapshot's clothes would invite reading
    "0 rounds" off a turn that had ten.
    """

    turnId: str
    parentTurnId: str | None = None
    agentId: str = "main"
    agentName: str = ""
    kind: str = "local"
    model: str = ""
    provider: str = ""
    startedAt: float = 0.0
    rounds: int = 0
    totalTokens: int = 0
    peerId: str | None = None


class TurnHistoryResponse(BaseModel):
    turns: list[TurnSummary] = Field(default_factory=list)


class AttentionSpec(BaseModel):
    """The attention block's shape. `kind` is derived from the head counts and is
    the single biggest driver of KV-cache size, which is why it's called out."""

    heads: int | None = None
    kvHeads: int | None = None
    headDim: int | None = None
    # True when headDim was computed as hidden/heads rather than read from the
    # metadata. That relationship holds for most models but NOT all — Gemma 3
    # decouples them (head_dim 256 with hidden 3840 / 16 heads = 240) — so a
    # derived value is shown as an estimate, never as a stated fact.
    headDimDerived: bool = False
    kind: str = "unknown"  # mha | gqa | mqa | unknown
    # Query heads per KV head. 1 = MHA, heads = MQA, anything between = GQA.
    groupRatio: int | None = None
    slidingWindow: int | None = None
    ropeTheta: float | None = None


class FfnSpec(BaseModel):
    """The per-block feed-forward network. `gated` changes the drawing: a gated
    FFN (SwiGLU/GeGLU) has two up-projections multiplied together, not one."""

    intermediateSize: int | None = None
    activation: str | None = None
    expansionRatio: float | None = None
    gated: bool | None = None


class MoeSpec(BaseModel):
    """Mixture-of-experts routing, when the model has it. `activeFraction` is the
    share of experts a single token actually passes through — the reason an MoE's
    active parameter count is far below its total."""

    experts: int | None = None
    expertsPerToken: int = 0
    expertIntermediateSize: int | None = None
    sharedExperts: int | None = None
    activeFraction: float | None = None


class ModelArchitecture(BaseModel):
    """The loaded model's structure, normalized across metadata sources.

    Every dimension is optional on purpose. A field we could not confirm stays
    `None` and is simply not drawn — inventing a plausible number would undermine
    the one thing this module is for.
    """

    # "ollama" — read off the running weights' GGUF metadata (highest confidence).
    # "huggingface" — read off a repo's config.json (structure, not your weights).
    # "none" — nothing available; `error` explains why.
    source: str = "none"
    sourceDetail: str = ""
    model: str = ""
    family: str | None = None
    parameterCount: int | None = None
    layers: int | None = None
    hiddenSize: int | None = None
    vocabSize: int | None = None
    contextLength: int | None = None
    tiedEmbeddings: bool | None = None
    normType: str | None = None
    attention: AttentionSpec | None = None
    ffn: FfnSpec | None = None
    moe: MoeSpec | None = None
    # Human-readable caveats worth showing beside the diagram (quantization,
    # multimodal towers, alternating attention patterns).
    notes: list[str] = Field(default_factory=list)
    error: str | None = None


class TensorEntry(BaseModel):
    """One tensor from the GGUF directory — the model as it exists on disk.

    Distinct from everything else in this file: the specs above are *descriptions*
    of a model normalized out of scalar metadata, whereas this is the inventory.
    `byteSize` is what a shape alone cannot tell you, because a quantized tensor's
    footprint depends on its block format.
    """

    name: str
    shape: list[int] = Field(default_factory=list)
    dtype: str
    elements: int
    # None when the ggml type id is one we have no block size for. A guessed size
    # would misattribute where the model's weight actually sits, which is the one
    # question the inventory exists to answer.
    byteSize: int | None = None
    # Transformer block index, or None for the tensors outside the stack
    # (embeddings, final norm, output head).
    layer: int | None = None
    component: str = "other"


class ModelTensorsResponse(BaseModel):
    """The loaded model's real tensor inventory, read from its GGUF header.

    `source` is `gguf` only when we opened the actual file the server loaded. There
    is no fallback source: unlike the architecture, an inventory cannot be
    approximated from a repo's config, so the pane shows nothing rather than
    something plausible.
    """

    source: str = "none"  # gguf | none
    path: str = ""
    fileSize: int | None = None
    ggufVersion: int | None = None
    tensorCount: int = 0
    # Highest `blk.N` index + 1. Reported separately from ModelArchitecture.layers
    # so the two disagreeing is visible rather than reconciled.
    layerCount: int | None = None
    totalParameters: int = 0
    totalBytes: int = 0
    # False when at least one tensor had an unrecognized quantization type, so
    # `totalBytes` is a floor rather than the total.
    bytesComplete: bool = True
    # Quantization type name → how many tensors use it. A mixed quant (most K-quant
    # builds keep some tensors at F32/F16) is the norm, not an anomaly.
    quantTypes: dict[str, int] = Field(default_factory=dict)
    tensors: list[TensorEntry] = Field(default_factory=list)
    error: str | None = None


class ModelInfoResponse(BaseModel):
    """What the provider reports about the loaded model itself."""

    model: str = ""
    provider: str = ""
    contextLength: int | None = None
    template: str | None = None
    parameters: str | None = None
    family: str | None = None
    error: str | None = None
