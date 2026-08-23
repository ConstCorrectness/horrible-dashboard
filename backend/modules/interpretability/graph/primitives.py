"""The `nn.Module` classes a generated file needs but `torch.nn` does not ship.

These are **emitted into the generated file**, not imported from a helper package.
That is a deliberate trade: the file is a few dozen lines longer, and in exchange it
is genuinely self-contained — you can copy it into a Kaggle notebook, a colleague's
repo, or a training project that never heard of this app, and it runs. A generated
file that secretly depends on `horrible_train` being installed would be a hidden
coupling exactly where the user is most likely to move the file somewhere else.
It is the same stance `recipes.py` takes about its cells: they're yours on landing.

Only the primitives a graph actually uses are emitted, in dependency order.
"""

from __future__ import annotations

#: Primitive name → (source, names it needs emitted before it).
PRIMITIVES: dict[str, tuple[str, tuple[str, ...]]] = {}


def _register(name: str, requires: tuple[str, ...], source: str) -> None:
    PRIMITIVES[name] = (source.strip("\n"), requires)


_register(
    "RMSNorm",
    (),
    '''
class RMSNorm(nn.Module):
    """Root-mean-square layer norm: LayerNorm without the mean subtraction."""

    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x):
        dtype = x.dtype
        x = x.float()
        x = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)
        return (x.to(dtype)) * self.weight
''',
)

_register(
    "RotaryEmbedding",
    (),
    '''
class RotaryEmbedding(nn.Module):
    """Rotary position embedding, applied to q and k.

    The cache is a buffer rather than a recomputation because it is the same for
    every layer and every step; `persistent=False` keeps it out of the state dict,
    where it would be dead weight in every checkpoint.
    """

    def __init__(self, head_dim: int, max_seq: int = 8192, theta: float = 10000.0):
        super().__init__()
        inv_freq = 1.0 / (theta ** (torch.arange(0, head_dim, 2).float() / head_dim))
        t = torch.arange(max_seq).float()
        freqs = torch.outer(t, inv_freq)
        self.register_buffer("cos", freqs.cos(), persistent=False)
        self.register_buffer("sin", freqs.sin(), persistent=False)

    @staticmethod
    def _rotate_half(x):
        x1, x2 = x.chunk(2, dim=-1)
        return torch.cat((-x2, x1), dim=-1)

    def forward(self, q, k):
        # q, k: [B, H, T, D]
        seq = q.shape[-2]
        cos = torch.cat((self.cos[:seq], self.cos[:seq]), dim=-1)[None, None]
        sin = torch.cat((self.sin[:seq], self.sin[:seq]), dim=-1)[None, None]
        q = q * cos + self._rotate_half(q) * sin
        k = k * cos + self._rotate_half(k) * sin
        return q, k
''',
)

_register(
    "MultiHeadAttention",
    ("RotaryEmbedding",),
    '''
class MultiHeadAttention(nn.Module):
    """Self-attention with grouped-query support.

    `kv_heads == heads` is MHA, `kv_heads == 1` is MQA, and anything between is GQA
    — one node, three behaviours, derived from the head counts exactly the way the
    model explorer's `AttentionSpec.kind` derives them from a GGUF's metadata.

    The KV heads are expanded with `repeat_interleave` rather than passed to
    `scaled_dot_product_attention`'s `enable_gqa`, which is new enough that a
    generated file would stop running on older torch for no benefit.
    """

    def __init__(
        self,
        dim: int,
        heads: int,
        kv_heads: int = 0,
        head_dim: int = 0,
        causal: bool = True,
        rope: bool = True,
        dropout: float = 0.0,
        bias: bool = False,
        max_seq: int = 8192,
        rope_theta: float = 10000.0,
    ):
        super().__init__()
        kv_heads = kv_heads or heads
        head_dim = head_dim or (dim // heads)
        if heads % kv_heads != 0:
            raise ValueError(f"heads ({heads}) must be a multiple of kv_heads ({kv_heads})")
        self.heads, self.kv_heads, self.head_dim = heads, kv_heads, head_dim
        self.causal, self.dropout = causal, dropout
        self.q_proj = nn.Linear(dim, heads * head_dim, bias=bias)
        self.k_proj = nn.Linear(dim, kv_heads * head_dim, bias=bias)
        self.v_proj = nn.Linear(dim, kv_heads * head_dim, bias=bias)
        self.o_proj = nn.Linear(heads * head_dim, dim, bias=bias)
        self.rope = RotaryEmbedding(head_dim, max_seq=max_seq, theta=rope_theta) if rope else None

    def forward(self, x, mask=None):
        b, t, _ = x.shape
        q = self.q_proj(x).view(b, t, self.heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(b, t, self.kv_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(b, t, self.kv_heads, self.head_dim).transpose(1, 2)
        if self.rope is not None:
            q, k = self.rope(q, k)
        if self.kv_heads != self.heads:
            repeat = self.heads // self.kv_heads
            k = k.repeat_interleave(repeat, dim=1)
            v = v.repeat_interleave(repeat, dim=1)
        out = F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask, is_causal=self.causal and mask is None,
            dropout_p=self.dropout if self.training else 0.0,
        )
        out = out.transpose(1, 2).reshape(b, t, self.heads * self.head_dim)
        return self.o_proj(out)
''',
)

_register(
    "SwiGLU",
    (),
    '''
class SwiGLU(nn.Module):
    """The gated feed-forward network: two up-projections multiplied together.

    Gated and dense FFNs are different drawings and different parameter counts —
    three matrices here, two in an `MLP` — which is why they are separate nodes
    rather than a checkbox.
    """

    def __init__(self, dim: int, hidden: int, bias: bool = False):
        super().__init__()
        self.gate_proj = nn.Linear(dim, hidden, bias=bias)
        self.up_proj = nn.Linear(dim, hidden, bias=bias)
        self.down_proj = nn.Linear(hidden, dim, bias=bias)

    def forward(self, x):
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))
''',
)

_register(
    "GeGLU",
    (),
    '''
class GeGLU(nn.Module):
    """SwiGLU with a GELU gate."""

    def __init__(self, dim: int, hidden: int, bias: bool = False):
        super().__init__()
        self.gate_proj = nn.Linear(dim, hidden, bias=bias)
        self.up_proj = nn.Linear(dim, hidden, bias=bias)
        self.down_proj = nn.Linear(hidden, dim, bias=bias)

    def forward(self, x):
        return self.down_proj(F.gelu(self.gate_proj(x)) * self.up_proj(x))
''',
)

_register(
    "MLP",
    (),
    '''
class MLP(nn.Module):
    """The dense (ungated) feed-forward network."""

    def __init__(self, dim: int, hidden: int, activation: str = "gelu", bias: bool = True):
        super().__init__()
        self.up_proj = nn.Linear(dim, hidden, bias=bias)
        self.down_proj = nn.Linear(hidden, dim, bias=bias)
        self.act = getattr(F, activation)

    def forward(self, x):
        return self.down_proj(self.act(self.up_proj(x)))
''',
)

_register(
    "MoE",
    ("SwiGLU",),
    '''
class MoE(nn.Module):
    """Top-k mixture of experts over SwiGLU experts.

    Written as a dense gather rather than a dispatch loop: it is slower per token
    and dramatically easier to read, and this file is a design you are about to
    edit, not a serving kernel.
    """

    def __init__(self, dim: int, hidden: int, experts: int = 8, top_k: int = 2, bias: bool = False):
        super().__init__()
        self.top_k = top_k
        self.router = nn.Linear(dim, experts, bias=False)
        self.experts = nn.ModuleList([SwiGLU(dim, hidden, bias=bias) for _ in range(experts)])

    def forward(self, x):
        logits = self.router(x)
        weights, idx = torch.topk(logits.softmax(-1), self.top_k, dim=-1)
        out = torch.zeros_like(x)
        for slot in range(self.top_k):
            for e, expert in enumerate(self.experts):
                hit = idx[..., slot] == e
                if hit.any():
                    out[hit] += expert(x[hit]) * weights[..., slot][hit].unsqueeze(-1)
        return out
''',
)


def resolve(names: set[str]) -> list[str]:
    """Every primitive the given ones need, in a definition-safe order.

    A class whose dependency is emitted after it is a `NameError` at import time,
    which is a failure the user would read as "the generated code is broken" rather
    than "the generator ordered two definitions wrongly".
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen or name not in PRIMITIVES:
            return
        seen.add(name)
        for dep in PRIMITIVES[name][1]:
            visit(dep)
        ordered.append(name)

    for name in sorted(names):
        visit(name)
    return ordered


def source_for(names: set[str]) -> str:
    """The primitive block for a generated file, or an empty string."""
    return "\n\n\n".join(PRIMITIVES[n][0] for n in resolve(names))
