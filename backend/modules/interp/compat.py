"""Does an artifact fit a model? Three answers, never two.

An SAE is a dictionary for one model's residual stream at one layer. Applied to a
different model — or the right model's sibling (base vs instruction-tuned) — it still
produces numbers, plausible-looking ones, and every one of them is wrong. So the check
is strict about what it can see and honest about what it cannot:

- **incompatible** — a stated fact contradicts the model: trained on another repo,
  an input width that is not this model's `d_model`, a layer it does not have.
- **compatible** — the trained-on repo matches, and nothing stated contradicts it.
- **unknown** — no contradiction, but not enough stated to say yes: a GGUF whose
  header names no base model, an artifact whose config gives no width.

Targets are the model the agent runs (its GGUF header, via the interpretability
module) and the model the worker holds (an HF repo). An artifact can fit one and not
the other; the Library shows both.

**A GGUF's `general.base_model` is its parent, not itself.** Gemma 3 1B IT's GGUF
names `google/gemma-3-1b-pt` there — the model it was fine-tuned *from*. Matching on
it called a Gemma Scope SAE trained on the IT model incompatible with the IT model,
while that SAE reproduced Neuronpedia's activations on it within 1%. A GGUF's own
identity is its name parts (`basename`-`size_label`-`finetune` → `gemma-3-1b-it`),
a weaker claim than a repo and labelled as one; an artifact trained on the parent
is `unknown` — close weights, not the same ones.
"""

from __future__ import annotations

from typing import Any


def _repo(value: str | None) -> str | None:
    """`https://huggingface.co/google/gemma-3-1b-it` / `google/gemma-3-1b-it` →
    `google/gemma-3-1b-it`, lowercased for comparison."""
    if not value:
        return None
    if "huggingface.co/" in value:
        value = value.split("huggingface.co/", 1)[1]
    parts = [p for p in value.strip("/").split("/") if p]
    return "/".join(parts[:2]).lower() if len(parts) >= 2 else None


def check(artifact: dict[str, Any], target: dict[str, Any] | None) -> dict[str, Any]:
    """`{state, reasons}` for one artifact against one target `{repo, dModel, layers}`."""
    if not target:
        return {"state": "unknown", "reasons": ["no model to check against"]}
    facts = artifact.get("facts") or {}
    reasons: list[str] = []
    mismatch = False

    trained_on = _repo(artifact.get("modelRepo"))
    model_repo = _repo(target.get("repo"))
    name = str(target.get("name") or "").lower()
    parents = {_repo(p) for p in target.get("parents") or []} - {None}
    repo_ok: bool | None = None
    if trained_on and model_repo:
        repo_ok = trained_on == model_repo
        if not repo_ok:
            mismatch = True
            reasons.append(
                f"trained on {artifact.get('modelRepo')}, this is {target.get('repo')}"
            )
    elif trained_on and name:
        if trained_on.split("/", 1)[1] == name:
            repo_ok = True
            reasons.append(f"matched by the file's own name ({name}), not a repo")
        elif trained_on in parents:
            reasons.append(
                f"trained on {artifact.get('modelRepo')}, the model this was fine-tuned "
                "from — close weights, not the same ones"
            )
        else:
            reasons.append(
                f"trained on {artifact.get('modelRepo')}; this file names itself {name}"
            )
    elif not model_repo and not name:
        reasons.append("the model does not name the repo it came from")
    else:
        reasons.append("the artifact does not name the model it was trained on")

    d_in, d_model = facts.get("dIn"), target.get("dModel")
    if d_in and d_model and d_in != d_model:
        mismatch = True
        reasons.append(f"input width {d_in} ≠ the model's d_model {d_model}")

    layer, layers = facts.get("layer"), target.get("layers")
    if layer is not None and layers and layer >= layers:
        mismatch = True
        reasons.append(f"layer {layer} does not exist (the model has {layers})")

    if mismatch:
        state = "incompatible"
    elif repo_ok:
        state = "compatible"
    else:
        state = "unknown"
    return {"state": state, "reasons": reasons}


def gguf_target(metadata: dict[str, Any], path: str) -> dict[str, Any]:
    """The agent's model as a target, from its GGUF header."""

    def suffix(key: str) -> Any:
        return next((v for k, v in metadata.items() if k.endswith("." + key)), None)

    parts = [
        metadata.get("general.basename"),
        metadata.get("general.size_label"),
        metadata.get("general.finetune"),
    ]
    count = metadata.get("general.base_model.count")
    parents = [
        metadata.get(f"general.base_model.{i}.repo_url")
        for i in range(int(count) if isinstance(count, int) else 1)
    ]
    return {
        "source": "gguf",
        "label": path.replace("\\", "/").rsplit("/", 1)[-1],
        # Only a key that names *this* model's repo; `base_model` is its parent.
        "repo": metadata.get("general.source.repo_url"),
        "name": "-".join(str(p) for p in parts if p) if parts[0] else None,
        "parents": [p for p in parents if p],
        "dModel": suffix("embedding_length"),
        "layers": suffix("block_count"),
    }
