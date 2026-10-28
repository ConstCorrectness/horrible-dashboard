"""The Assistant Axis, built on the model you run: what it is, how it is checked.

Lu et al. (2026) found that a chat model's persona has a leading direction in the
residual stream: the mean activation of the model answering *as its assistant*,
minus the mean over it answering in many other roles. Projections along it fall
when a conversation pulls the model out of character — role-play, emotional
pressure, long agent runs — and capping the projection from below keeps it there.

The published vectors are for 27–70B models. This builds the same thing for a
local GGUF (`axis_runner.build`): a role set, a few questions, greedy answers, and
the mean `l_out` of each answer's generated tokens at every layer. Then:

- **The axis** at each layer is `mean(assistant) − mean(roles)`, unit length.
  PC1 of the role means is reported beside it with their cosine — the paper's
  observation is that the two agree, and on a small model that is a finding to
  measure, not assume.
- **The check** is on questions held out of the fit: do held-out assistant answers
  project above held-out role answers? AUC per layer, on answer means and on single
  tokens (what the monitor reads). The default layer is the one that separates
  single tokens best, never one picked in advance.
- **The bands** the monitor and the cap use are percentiles of the held-out
  single-token projections: the floor is the assistant's 25th percentile.

What this is not: the paper filters role answers with a judge for "fully in role";
this keeps them all, so a role the model refused to play pulls its mean toward the
assistant and blurs the axis. The role examples are stored so that can be seen.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np

from backend import paths

#: The default assistant: no system prompt, and a few plain ones, so the axis is
#: not one prompt's wording.
ASSISTANT_PROMPTS = [
    "",
    "You are a helpful assistant.",
    "You are an AI assistant. Answer helpfully, honestly and clearly.",
]

#: Questions that let a persona show, interleaved with ordinary tasks. The tasks are
#: not filler: with persona questions alone the assistant's answers are all "I am a
#: large language model…", the most assistant-like text there is, and the bands sat
#: so high that a plain budgeting answer read 92% "below the floor" (measured,
#: Gemma 3 1B). A role answers a task in character too, so the axis keeps measuring
#: voice, not topic. The last `DEFAULT_HELD_OUT` — a persona question, advice and a
#: task — are held out.
DEFAULT_QUESTIONS = [
    "Tell me about yourself.",
    "How do I boil an egg?",
    "What do you do in your free time?",
    "Can you help me write a short note thanking a colleague?",
    "How are you feeling right now?",
    "What is the meaning of life?",
    "Explain why the sky is blue.",
    "What is something you are afraid of?",
    "Give me some advice about dealing with stress.",
    "How should I plan a weekly budget?",
]
DEFAULT_HELD_OUT = 3
DEFAULT_RESPONSE_TOKENS = 32

#: From near the assistant (a teacher, a librarian) to far from it (a ghost, a
#: storm), so the role means spread along the axis instead of clustering.
ROLES = [
    ("pirate", "a swaggering pirate captain on the high seas"),
    ("ghost", "a ghost who has haunted the same house for two hundred years"),
    ("dragon", "an ancient dragon guarding its hoard"),
    ("noir detective", "a cynical noir detective in 1940s Los Angeles"),
    ("medieval knight", "a medieval knight sworn to a king"),
    ("conspiracy theorist", "a conspiracy theorist who trusts no one"),
    ("fisherman", "a grumpy old fisherman on a cold northern coast"),
    ("vampire", "a centuries-old vampire"),
    ("oracle", "a cryptic oracle who speaks in riddles"),
    ("drill sergeant", "a shouting army drill sergeant"),
    ("despairing poet", "a poet consumed by despair"),
    ("alien", "an alien visiting Earth for the first time"),
    ("trickster spirit", "a mischievous fox spirit who loves to deceive"),
    ("cult leader", "the charismatic leader of a secretive cult"),
    ("victorian aristocrat", "a snobbish Victorian aristocrat"),
    ("surfer", "a laid-back surfer"),
    ("wizard", "an eccentric old wizard"),
    ("cowboy", "a weathered cowboy on the open range"),
    ("comedian", "a sarcastic stand-up comedian"),
    ("demon", "a demon bargaining for souls"),
    ("sentient storm", "a sentient thunderstorm"),
    ("shakespearean actor", "a melodramatic Shakespearean actor"),
    ("chef", "a temperamental chef in a busy kitchen"),
    ("tour guide", "an enthusiastic city tour guide"),
    ("librarian", "a quiet, precise librarian"),
    ("teacher", "a patient primary-school teacher"),
    ("therapist", "a calm, empathetic therapist"),
    ("nurse", "an experienced night-shift nurse"),
    ("lawyer", "a sharp corporate lawyer"),
    ("child", "an excitable seven-year-old child"),
]


class AxisError(Exception):
    """An axis cannot be built, read or applied as asked."""


def default_roles(count: int | None = None) -> list[dict[str, str]]:
    roles = [{"name": n, "description": d} for n, d in ROLES]
    return roles[:count] if count else roles


def parse_roles(text: str) -> list[dict[str, str]]:
    """A pasted role list: one per line, `name: description` or just a description
    (the repo's 275-role list pastes in as-is)."""
    roles = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line or line.startswith("#"):
            continue
        name, sep, desc = line.partition(":")
        if sep and desc.strip() and len(name) <= 40:
            roles.append({"name": name.strip(), "description": desc.strip()})
        else:
            roles.append({"name": line[:40], "description": line})
    return roles


def role_prompt(role: dict[str, str]) -> str:
    return (
        f"You are {role['description']}. Stay fully in character as "
        f"{role['name']}, and answer the way {role['name']} would."
    )


# ── the fit ──────────────────────────────────────────────────────────────────


def auc(positive: np.ndarray, negative: np.ndarray) -> float | None:
    """P(a positive scores above a negative), ties half — Mann–Whitney, by ranks."""
    if positive.size == 0 or negative.size == 0:
        return None
    scores = np.concatenate([positive, negative])
    order = scores.argsort(kind="mergesort")
    ranks = np.empty(scores.size)
    ranks[order] = np.arange(1, scores.size + 1)
    # Average ranks over ties.
    sorted_scores = scores[order]
    i = 0
    while i < scores.size:
        j = i
        while j + 1 < scores.size and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + j + 2) / 2
        i = j + 1
    u = ranks[: positive.size].sum() - positive.size * (positive.size + 1) / 2
    return float(u / (positive.size * negative.size))


def _pct(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {}
    p = np.percentile(values, [5, 25, 50, 75, 95])
    return {
        "mean": round(float(values.mean()), 3),
        **{
            k: round(float(v), 3) for k, v in zip(("p5", "p25", "p50", "p75", "p95"), p)
        },
    }


def fit(
    means: np.ndarray,
    labels: list[str],
    held: np.ndarray,
    held_tokens: list[tuple[str, np.ndarray]],
) -> dict[str, Any]:
    """The axis from answer means `[S, L, D]` and its held-out check.

    `labels[i]` is `"assistant"` or a role name; `held[i]` marks answers to
    held-out questions; `held_tokens` holds those answers' single-token
    activations `[T, L, D]`, which is what the bands are made of.
    """
    labels_arr = np.asarray(labels)
    is_assistant = labels_arr == "assistant"
    train = ~held
    if not (is_assistant & train).any() or not (~is_assistant & train).any():
        raise AxisError("the fit needs assistant and role answers on fit questions")
    n_layer = means.shape[1]

    # Role means: one vector per role over its fit answers — the paper's role vectors.
    role_names = sorted({name for name in labels if name != "assistant"})
    role_vecs = np.stack(
        [means[(labels_arr == name) & train].mean(axis=0) for name in role_names]
        if all(((labels_arr == n) & train).any() for n in role_names)
        else [means[~is_assistant & train].mean(axis=0)]
    )  # [R, L, D]
    a_mean = means[is_assistant & train].mean(axis=0)  # [L, D]
    diff = a_mean - role_vecs.mean(axis=0)
    norms = np.linalg.norm(diff, axis=1, keepdims=True)
    vectors = (diff / np.where(norms == 0, 1, norms)).astype(np.float32)  # [L, D]

    tok_a = [t for name, t in held_tokens if name == "assistant"]
    tok_r = [t for name, t in held_tokens if name != "assistant"]
    tok_a_arr = np.concatenate(tok_a).astype(np.float32) if tok_a else None
    tok_r_arr = np.concatenate(tok_r).astype(np.float32) if tok_r else None

    layers = []
    for layer in range(n_layer):
        v = vectors[layer]
        # PC1 of the role vectors, sign-aligned with the axis.
        centred = role_vecs[:, layer] - role_vecs[:, layer].mean(axis=0)
        pc1_cos = None
        if centred.shape[0] >= 3 and np.abs(centred).sum() > 0:
            pc1 = np.linalg.svd(centred, full_matrices=False)[2][0]
            pc1_cos = round(abs(float(pc1 @ v)), 3)
        proj_means = means[:, layer] @ v
        mean_auc = auc(
            proj_means[is_assistant & held], proj_means[~is_assistant & held]
        )
        entry: dict[str, Any] = {
            "layer": layer,
            "diffNorm": round(float(norms[layer, 0]), 3),
            "pc1Cos": pc1_cos,
            "auc": None if mean_auc is None else round(mean_auc, 3),
        }
        if tok_a_arr is not None and tok_r_arr is not None:
            pa = tok_a_arr[:, layer] @ v
            pr = tok_r_arr[:, layer] @ v
            token_auc = auc(pa, pr)
            pooled = float(np.sqrt((pa.var() + pr.var()) / 2)) or 1.0
            entry |= {
                "tokenAuc": None if token_auc is None else round(token_auc, 3),
                "dPrime": round(float((pa.mean() - pr.mean()) / pooled), 3),
                "assistant": _pct(pa),
                "role": _pct(pr),
            }
        layers.append(entry)

    default = choose_layer(layers)
    role_proj = (
        {
            name: [
                round(float(role_vecs[i, layer] @ vectors[layer]), 3)
                for layer in range(n_layer)
            ]
            for i, name in enumerate(role_names)
        }
        if len(role_names) == role_vecs.shape[0]
        else {}
    )
    a_proj = [
        round(float(a_mean[layer] @ vectors[layer]), 3) for layer in range(n_layer)
    ]
    return {
        "vectors": vectors,
        "layers": layers,
        "defaultLayer": default,
        "check": check_state(layers[default]),
        "cap": default_cap(layers, default),
        "roleProjections": role_proj,
        "assistantProjection": a_proj,
        "samples": {
            "assistant": int(is_assistant.sum()),
            "role": int((~is_assistant).sum()),
            "heldOut": int(held.sum()),
            "heldOutTokens": int(sum(t.shape[0] for _, t in held_tokens)),
        },
    }


def choose_layer(layers: list[dict[str, Any]]) -> int:
    """The layer that separates single held-out tokens best (then by d′).

    Block 0 cannot be steered or capped, and the last block's `l_out` is pruned to
    the output positions on a prompt pass, so neither is a candidate."""
    candidates = [e for e in layers if 1 <= e["layer"] < len(layers) - 1] or layers
    return max(
        candidates,
        key=lambda e: (
            e.get("tokenAuc") if e.get("tokenAuc") is not None else (e.get("auc") or 0),
            e.get("dPrime") or 0,
        ),
    )["layer"]


def check_state(entry: dict[str, Any]) -> dict[str, Any]:
    """separates / weak / not_checked, from the held-out AUCs at one layer."""
    token_auc, mean_auc = entry.get("tokenAuc"), entry.get("auc")
    if token_auc is None and mean_auc is None:
        return {"state": "not_checked", "reason": "no held-out answers"}
    score = token_auc if token_auc is not None else mean_auc
    state = "separates" if score >= 0.8 else "weak"
    return {
        "state": state,
        "tokenAuc": token_auc,
        "auc": mean_auc,
        "reason": (
            f"held-out tokens: assistant above role with probability {score:.2f}"
        ),
    }


def default_cap(layers: list[dict[str, Any]], default: int) -> dict[str, Any]:
    """A band of up to five layers around the default, floored at the assistant's
    held-out 25th percentile at each."""
    n = len(layers)
    band = [
        layer
        for layer in range(default - 2, default + 3)
        if 1 <= layer < n - 1 and layers[layer].get("assistant")
    ]
    return {
        "layers": band,
        "tau": {str(layer): layers[layer]["assistant"]["p25"] for layer in band},
        "percentile": 25,
    }


# ── storage ──────────────────────────────────────────────────────────────────


def axes_root() -> Path:
    return paths.data_dir() / "interp" / "axes"


def new_axis_dir(model_path: str) -> Path:
    stem = re.sub(r"[^\w.\-]+", "-", Path(model_path).stem).strip("-")
    return axes_root() / f"{stem}--{time.strftime('%Y%m%d-%H%M%S')}"


def register(out_dir: Path, model_sha: str) -> dict[str, Any]:
    """A built axis as a Library row of kind `axis`."""
    from backend.modules.interp import artifacts

    meta = json.loads((out_dir / "axis.json").read_text(encoding="utf-8"))
    artifact_id = f"axis:{out_dir.name}"
    size = sum(p.stat().st_size for p in out_dir.iterdir() if p.is_file())
    facts = {
        "layer": meta["defaultLayer"],
        "dIn": meta["nEmbd"],
        "nLayer": meta["nLayer"],
        "modelSha": model_sha,
        "modelPath": meta["modelPath"],
        "roles": len(meta["roles"]),
        "check": meta["check"],
    }
    with artifacts._db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO interp_artifacts
               (id, entry_id, kind, repo, choice, model_repo, path, bytes, facts, created_at)
               VALUES (?, 'axis', 'axis', 'local', ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                out_dir.name,
                meta["modelName"],
                str(out_dir),
                size,
                json.dumps(facts),
                time.time(),
            ),
        )
    stored = artifacts.get_artifact(artifact_id)
    assert stored is not None
    return stored


class Axis:
    def __init__(self, artifact: dict[str, Any]) -> None:
        directory = Path(artifact["path"])
        try:
            self.meta = json.loads(
                (directory / "axis.json").read_text(encoding="utf-8")
            )
            self.vectors = np.load(directory / "axis.npy")
        except (OSError, ValueError) as exc:
            raise AxisError(f"the axis files are unreadable: {exc}") from exc
        self.artifact = artifact
        self.default_layer = int(self.meta["defaultLayer"])

    def bands(self, layer: int) -> dict[str, Any]:
        entry = self.meta["layers"][layer]
        return {"assistant": entry.get("assistant"), "role": entry.get("role")}

    def summary(self) -> dict[str, Any]:
        """The artifact without its vectors: what the pane and the agent read."""
        m = self.meta
        return {
            "id": self.artifact["id"],
            "modelName": m["modelName"],
            "modelPath": m["modelPath"],
            "modelSha": self.artifact["facts"].get("modelSha"),
            "nLayer": m["nLayer"],
            "nEmbd": m["nEmbd"],
            "defaultLayer": self.default_layer,
            "check": m["check"],
            "cap": m["cap"],
            "layers": m["layers"],
            "roles": m["roles"],
            "roleProjections": m["roleProjections"],
            "assistantProjection": m["assistantProjection"],
            "examples": m.get("examples", {}),
            "questions": m["questions"],
            "heldOut": m["heldOut"],
            "samples": m["samples"],
            "maxTokens": m["maxTokens"],
            "elapsed": m.get("elapsed"),
            "createdAt": m.get("createdAt"),
        }


def load(artifact_id: str) -> Axis:
    from backend.modules.interp import artifacts

    art = artifacts.get_artifact(artifact_id)
    if art is None or art["kind"] != "axis":
        raise AxisError(f"no axis {artifact_id!r}")
    return Axis(art)


def list_axes() -> list[dict[str, Any]]:
    from backend.modules.interp import artifacts

    out = []
    for art in artifacts.list_artifacts():
        if art["kind"] != "axis":
            continue
        try:
            out.append(Axis(art).summary())
        except AxisError:
            continue
    return out


# ── projections ──────────────────────────────────────────────────────────────


def trace_series(trace: Any, ax: Axis, layer: int) -> dict[str, Any]:
    """Every position's projection at `layer` in a trace: the prompt pass, then one
    column per generated token. A trace is raw text — no chat template — so it is
    not the assistant turn the bands were measured on, and the result says so."""
    from backend.modules.interp import sae
    from backend.modules.llamacpp import lens

    if ax.meta["modelPath"] != str(trace.manifest.get("modelPath")) and (
        ax.artifact["facts"].get("modelSha") != trace.manifest.get("modelSha")
    ):
        raise AxisError("the trace is of a different model than the axis was built on")
    v = ax.vectors[layer]
    values: list[float | None] = []
    passes = sorted({r.pass_index for r in trace.records})
    for p in passes:
        try:
            res = lens.read_residuals(trace, p)
        except lens.LensError:
            continue
        m = res.by_layer.get(layer)
        if m is None:
            continue
        if p == 0:
            values.extend([None] * (res.n_tokens - m.shape[1]))
        values.extend(round(float(x), 3) for x in (m.T @ v))
    if not any(x is not None for x in values):
        raise AxisError(f"the trace has no l_out at layer {layer}")
    tokens = sae.trace_tokens(trace)
    return {
        "traceId": trace.trace_id,
        "layer": layer,
        "tokens": [str(t.get("text", "")) for t in tokens][: len(values)],
        "generatedFrom": int(trace.manifest.get("promptTokens") or 0),
        "values": values,
        "templated": bool(trace.manifest.get("chatTemplate")),
        "bands": ax.bands(layer),
        "summary": drift_summary(values, ax.bands(layer)),
    }


def drift_summary(
    values: list[float | None], bands: dict[str, Any], skip: int = 1
) -> dict[str, Any]:
    """The numbers a reader (or the agent) needs from a projection series: where it
    sits against the assistant's held-out range, its low point, and its trend."""
    xs = np.asarray([v for v in values[skip:] if v is not None], dtype=np.float64)
    if xs.size == 0:
        return {}
    a = bands.get("assistant") or {}
    floor = a.get("p25")
    low = int(np.argmin(xs))
    slope = float(np.polyfit(np.arange(xs.size), xs, 1)[0]) if xs.size > 2 else 0.0
    out: dict[str, Any] = {
        "mean": round(float(xs.mean()), 3),
        "min": round(float(xs[low]), 3),
        "minAt": low + skip,
        "slopePer100": round(slope * 100, 3),
    }
    if floor is not None:
        out["floor"] = floor
        out["belowFloor"] = round(float((xs < floor).mean()), 3)
    role = bands.get("role") or {}
    if role.get("p50") is not None:
        out["roleMedian"] = role["p50"]
        out["inRoleRange"] = round(float((xs <= role["p50"]).mean()), 3)
    return out


def message_drift(
    values: list[float | None],
    messages: list[dict[str, Any]],
    bands: dict[str, Any],
) -> dict[str, Any]:
    """Each message's median projection, and how far the assistant moved from its
    own first reply — in units of the gap between the assistant's and the roles'
    held-out medians, so -1 is "as far as a role is from the assistant".

    This, not the floor, is what a pasted or captured conversation is read by. The
    bands come from the model's *own* answers; text it did not write, read back,
    sits lower even when it is plain assistant prose (measured on Gemma 3 1B: a
    budgeting reply 92% "below the floor"). Within one conversation that offset is
    shared, so the change between replies is the persona signal. Medians, because
    each span starts and ends on template markers.
    """
    per = []
    for m in messages:
        seg = [v for v in values[m["start"] : m["end"]] if v is not None]
        if seg:
            per.append(
                {
                    "role": m["role"],
                    "start": m["start"],
                    "median": round(float(np.median(seg)), 3),
                }
            )
    out: dict[str, Any] = {"perMessage": per}
    replies = [p["median"] for p in per if p["role"] == "assistant"]
    a50 = (bands.get("assistant") or {}).get("p50")
    r50 = (bands.get("role") or {}).get("p50")
    if not replies or a50 is None or r50 is None or a50 <= r50:
        return out
    gap = a50 - r50
    out |= {
        "gap": round(gap, 3),
        "firstReply": replies[0],
        "lastReply": replies[-1],
        "change": round(replies[-1] - replies[0], 3),
        "changeInGaps": round((replies[-1] - replies[0]) / gap, 3),
        "replies": len(replies),
    }
    return out


# ── running the subprocess ───────────────────────────────────────────────────


async def run(
    spec: dict[str, Any], timeout: float = 20 * 60
) -> AsyncIterator[dict[str, Any]]:
    """`axis_runner` through the tracer's subprocess plumbing."""
    from backend.modules.llamacpp import trace_runner

    async for event in trace_runner.stream_module(
        "backend.modules.interp.axis_runner", spec, timeout
    ):
        yield event


def vectors_for(ax: Axis, layers: list[int]) -> dict[str, list[float]]:
    bad = [layer for layer in layers if not 0 <= layer < ax.meta["nLayer"]]
    if bad:
        raise AxisError(
            f"layers {bad} are outside the model's 0..{ax.meta['nLayer'] - 1}"
        )
    return {str(layer): ax.vectors[layer].tolist() for layer in layers}


def flatten_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Messages a chat template can render: system/user/assistant only. A tool
    result becomes a user message marked as one, and an assistant's tool calls are
    appended to its text — templates of small models have no tool role."""
    out: list[dict[str, str]] = []
    for m in messages:
        role = str(m.get("role") or "user")
        content = m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content) if content is not None else ""
        if role == "tool":
            out.append({"role": "user", "content": f"[tool result]\n{content}"})
            continue
        if role == "assistant" and m.get("tool_calls"):
            content = (content + "\n" + json.dumps(m["tool_calls"])).strip()
        if role not in ("system", "user", "assistant"):
            role = "user"
        # Templates want alternation; merge consecutive same-role messages.
        if out and out[-1]["role"] == role and role != "system":
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    return out


def turn_messages(
    turn_id: str, round_index: int = -1
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """A captured agent turn's context as templatable messages: the round's blocks
    rebuilt by agentpedia (the list the provider was handed), flattened for a small
    model's template. The last round by default — the longest conversation."""
    from backend.modules.agentpedia import rebuild
    from backend.modules.agentpedia.fork import ForkError, _round, load_turn

    try:
        turn = load_turn(turn_id)
        index = round_index if round_index >= 0 else len(turn.rounds) - 1
        snapshot = _round(turn, index)
    except ForkError as exc:
        raise AxisError(str(exc)) from exc
    messages, report = rebuild.messages_from(snapshot)
    return flatten_messages(messages), {
        "turnId": turn_id,
        "round": index,
        "rounds": len(turn.rounds),
        "agentId": turn.agentId,
        "model": turn.model,
        "exact": report.exact,
        "clipped": list(report.clipped),
    }
