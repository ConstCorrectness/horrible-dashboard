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
    repo_ok: bool | None = None
    if trained_on and model_repo:
        repo_ok = trained_on == model_repo
        if not repo_ok:
            mismatch = True
            reasons.append(
                f"trained on {artifact.get('modelRepo')}, this is {target.get('repo')}"
            )
    elif not model_repo:
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

    return {
        "source": "gguf",
        "label": path.replace("\\", "/").rsplit("/", 1)[-1],
        "repo": metadata.get("general.base_model.0.repo_url"),
        "dModel": suffix("embedding_length"),
        "layers": suffix("block_count"),
    }
