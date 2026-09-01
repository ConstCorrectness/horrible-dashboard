"""Does speculative decoding actually help *here*? Measure, do not assume.

This is the deliverable of the speculative-decoding work, not garnish. The feature
can genuinely be **negative**: on a single consumer card the draft model's VRAM
comes out of the target's KV cache, and drafting costs a forward pass per batch
whether or not the guesses are accepted. Whether that trade wins depends on the
pair of models, the card, and — most of all — the prompt.

Three rules follow, each of which is how you fool yourself otherwise:

- **Report p50 and p10, never a mean.** Variance is speculative decoding's
  defining property: it is fast when the continuation is predictable and slow when
  it is not. A mean hides that a code prompt got 2.4× and a prose prompt got 0.8×.
- **Report per prompt, never only aggregated.** An aggregate over a prompt set that
  happens to be mostly boilerplate is how you convince yourself of a speedup you
  will never see on your actual workload.
- **A/B on the same machine, back to back.** Tokens/sec is not comparable across
  runs on a laptop that thermally throttles, so the two configurations are measured
  in the same session against the same server binary.

The acceptance rate `llama-server` reports (when it does) is included because it
explains the result: a 20% acceptance rate that is slower is a *model pairing*
problem, and a 70% acceptance rate that is slower is a *memory* problem.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Deliberately mixed. Speculative decoding helps predictable continuations and
#: hurts unpredictable ones, so a set of only one kind produces a number that is
#: true and useless.
DEFAULT_PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "code",
        "Write a Python function that reverses a linked list. "
        "Include a docstring and type hints.",
    ),
    (
        "boilerplate",
        "Write a standard MIT licence header comment block for a file "
        "authored in 2026.",
    ),
    (
        "prose",
        "Describe an unfamiliar city at dawn, in three sentences, without naming it.",
    ),
    (
        "factual",
        "List the planets of the solar system in order from the sun.",
    ),
)

REQUEST_TIMEOUT_S = 300.0


def percentile(values: list[float], q: float) -> float:
    """Shared shape with `network/bench.py`: interpolated, on a sorted copy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


@dataclass
class PromptResult:
    label: str
    samples: list[float] = field(default_factory=list)
    accepted: list[float] = field(default_factory=list)
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "runs": len(self.samples),
            "errors": self.errors,
            "tokensPerSecondP50": round(percentile(self.samples, 0.50), 2),
            # p10, not min: one cold-cache outlier is not the tail that matters,
            # but a tenth of your requests being slow certainly is.
            "tokensPerSecondP10": round(percentile(self.samples, 0.10), 2),
            "acceptanceRate": (
                round(sum(self.accepted) / len(self.accepted), 3)
                if self.accepted
                else None
            ),
        }


async def _one_call(
    client: httpx.AsyncClient, endpoint: str, model: str, prompt: str, max_tokens: int
) -> tuple[float, float | None]:
    """One completion. Returns (tokens/sec, acceptance rate or None)."""
    started = time.perf_counter()
    res = await client.post(
        f"{endpoint}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # Greedy: sampling noise would show up as throughput variance and be
            # indistinguishable from the thing being measured.
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    res.raise_for_status()
    elapsed = time.perf_counter() - started
    body = res.json()

    usage = body.get("usage") or {}
    produced = int(usage.get("completion_tokens") or 0)
    if not produced or elapsed <= 0:
        raise ValueError("the server reported no completion tokens")

    # llama-server reports draft acceptance under a few names across versions;
    # absent is normal and must not be confused with zero.
    timings = body.get("timings") or {}
    accepted = None
    drafted = timings.get("draft_n") or timings.get("n_draft")
    taken = timings.get("draft_n_accepted") or timings.get("n_draft_accepted")
    if (
        isinstance(drafted, (int, float))
        and drafted
        and isinstance(taken, (int, float))
    ):
        accepted = float(taken) / float(drafted)

    return produced / elapsed, accepted


async def measure(
    endpoint: str,
    model: str,
    *,
    runs: int = 3,
    max_tokens: int = 128,
    prompts: tuple[tuple[str, str], ...] = DEFAULT_PROMPTS,
) -> dict[str, Any]:
    """Measure the currently-served configuration across the prompt set."""
    results: list[PromptResult] = []
    async with httpx.AsyncClient() as client:
        for label, prompt in prompts:
            entry = PromptResult(label=label)
            # One unrecorded warm-up: the first call pays for prompt processing
            # and cache allocation, and charging that to the configuration would
            # penalise whichever one happened to run first.
            try:
                await _one_call(client, endpoint, model, prompt, 16)
            except Exception:  # noqa: BLE001
                pass
            for _ in range(runs):
                try:
                    rate, accepted = await _one_call(
                        client, endpoint, model, prompt, max_tokens
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.info("specbench: %s failed (%s)", label, exc)
                    entry.errors += 1
                    continue
                entry.samples.append(rate)
                if accepted is not None:
                    entry.accepted.append(accepted)
            results.append(entry)

    everything = [s for r in results for s in r.samples]
    return {
        "endpoint": endpoint,
        "model": model,
        "runs": runs,
        "maxTokens": max_tokens,
        "prompts": [r.to_dict() for r in results],
        "overallP50": round(percentile(everything, 0.50), 2),
        "overallP10": round(percentile(everything, 0.10), 2),
    }


def compare(baseline: dict[str, Any], drafted: dict[str, Any]) -> dict[str, Any]:
    """Turn two `measure` results into a verdict, per prompt and overall.

    `verdict` may legitimately be "slower". A feature that does not pay off on this
    hardware and this prompt set is a *result*, and reporting it as one is the
    entire reason the bench exists.
    """
    by_label = {p["label"]: p for p in baseline.get("prompts", [])}
    rows = []
    for entry in drafted.get("prompts", []):
        base = by_label.get(entry["label"])
        if base is None:
            continue
        base_p50 = base["tokensPerSecondP50"] or 0.0
        rows.append(
            {
                "label": entry["label"],
                "baselineP50": base_p50,
                "draftedP50": entry["tokensPerSecondP50"],
                "speedup": (
                    round(entry["tokensPerSecondP50"] / base_p50, 3)
                    if base_p50
                    else None
                ),
                "acceptanceRate": entry["acceptanceRate"],
            }
        )

    base_overall = baseline.get("overallP50") or 0.0
    overall = (
        round((drafted.get("overallP50") or 0.0) / base_overall, 3)
        if base_overall
        else None
    )
    return {
        "perPrompt": rows,
        "overallSpeedup": overall,
        "verdict": _verdict(overall, rows),
    }


def _verdict(overall: float | None, rows: list[dict[str, Any]]) -> str:
    if overall is None:
        return "no baseline to compare against"
    if overall < 1.0:
        worst = min(rows, key=lambda r: r["speedup"] or 1.0, default=None)
        detail = f" (worst: {worst['label']})" if worst else ""
        return (
            f"slower overall ({overall}x){detail} — on this card the draft's memory "
            "likely costs more than its guesses save"
        )
    if overall < 1.15:
        return f"no meaningful change ({overall}x)"
    return f"faster ({overall}x)"
