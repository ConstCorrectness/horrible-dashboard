"""Synthetic rows: generating training data from a seed and a prompt.

The `synthesize` step in a build pipeline. Two engines behind one step:

- **A local model**, through the same `roster.resolve_provider` every agent turn
  uses. So "generate 200 paraphrases with the model I already have running" needs
  no key, no account and no network, which is the whole reason the node serves its
  own weights.
- **NVIDIA Nemo Data Designer**, when the `nvidia` connector is connected. This is
  the engine behind Unsloth Studio's Data Recipes, and it is much better at
  structured, constrained generation than a bare chat call. Optional and
  lazy-imported; absent, the step says so and falls back to the local engine rather
  than failing the build.

**Generated rows are marked.** Every row this step produces carries `synthetic:
true` and the model that made it. A synthetic set that cannot be told from a
collected one is how a benchmark ends up contaminated by its own training data, and
by the time that shows up as a suspiciously high score the provenance is gone.

**The seed rows go in the prompt, not just the count.** Generating "200 examples of
customer support conversations" from nothing yields 200 variations of one
imagined conversation. Grounding each batch in real rows is what makes the output
resemble the data it will be mixed with.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Rows generated per model call. Small batches because a model asked for 200 rows
#: in one response produces 200 increasingly similar ones, and a truncated JSON
#: array at the end costs the whole call.
BATCH = 10

#: Seed rows shown per call. Enough to convey the shape, few enough to leave room.
SEED_SAMPLE = 3


class SynthError(RuntimeError):
    """Generation could not run. Names which engine and why."""


def _prompt(params: dict[str, Any], seeds: list[dict[str, Any]], count: int) -> str:
    instruction = str(params.get("prompt") or "")
    shape = json.dumps(seeds[:SEED_SAMPLE], ensure_ascii=False, indent=2, default=str)
    return (
        f"{instruction}\n\n"
        f"Here are {min(len(seeds), SEED_SAMPLE)} real examples from the dataset, "
        f"to show the exact shape and register to match:\n\n{shape}\n\n"
        f"Generate {count} NEW examples in the same JSON shape. They must be "
        f"genuinely different from each other and from the examples above — vary "
        f"the subject, length and phrasing, not just the nouns.\n\n"
        f"Reply with a JSON array of {count} objects and nothing else."
    )


def _parse_array(text: str) -> list[dict[str, Any]]:
    """The JSON array out of a reply that may be wrapped in prose or a fence.

    Models fence JSON, prefix it with "Here are your examples:", or both. Refusing
    to parse those would make the step fail on output that is perfectly good; so
    the fence and the prose are stripped, and only a genuinely absent array is an
    error.
    """
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end <= start:
        raise SynthError("the model's reply contained no JSON array")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise SynthError(f"the model's reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise SynthError("the model returned an object rather than an array of rows")
    return [row for row in parsed if isinstance(row, dict)]


async def _generate_local(
    seeds: list[dict[str, Any]], params: dict[str, Any], count: int
) -> list[dict[str, Any]]:
    import httpx

    from backend.modules.agent import providers as P
    from backend.modules.agent.roster import resolve_provider
    from backend.modules.agent.routes import load_config

    config = load_config()
    info, endpoint = resolve_provider(config, str(params.get("agent") or "main"))
    model = str(params.get("model") or getattr(config, "model", "") or "")
    if not model:
        raise SynthError("no model is configured to generate with")

    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=180.0) as client:
        while len(out) < count:
            batch = min(BATCH, count - len(out))
            result = await P.chat(
                client,
                info,
                endpoint,
                model,
                [{"role": "user", "content": _prompt(params, seeds, batch)}],
                [],
            )
            rows = _parse_array(result.content)
            if not rows:
                # No progress means the next identical call makes none either.
                raise SynthError("the model produced no usable rows")
            for row in rows[:batch]:
                out.append({**row, "synthetic": True, "synthetic_model": model})
    return out[:count]


async def _generate_nemo(
    seeds: list[dict[str, Any]], params: dict[str, Any], count: int
) -> list[dict[str, Any]]:
    """NVIDIA Nemo Data Designer, when the connector and the extra are both there."""
    from backend.modules.connectors import store as connector_store

    record = connector_store.load("nvidia")
    if record is None or not record.get("access_token"):
        raise SynthError(
            "the NVIDIA connector is not connected, so Nemo Data Designer is "
            "unavailable. Connect it from the home page, or use the local engine."
        )
    try:
        from nemo_microservices import NeMoMicroservices  # noqa: PLC0415
    except ImportError as exc:
        raise SynthError(
            "Nemo Data Designer needs the `nemo` extra: `uv sync --extra nemo`. "
            "The local engine needs nothing."
        ) from exc

    client = NeMoMicroservices(
        base_url=str(params.get("baseUrl") or "https://integrate.api.nvidia.com/v1"),
        api_key=record["access_token"],
    )
    try:
        result = client.beta.data_designer.create(
            model=str(params.get("model") or "nvidia/nemotron-4-340b-instruct"),
            num_records=count,
            prompt=_prompt(params, seeds, count),
        )
    except Exception as exc:  # noqa: BLE001 — vendor errors are shown verbatim
        raise SynthError(f"Nemo Data Designer failed: {exc}") from exc
    rows = getattr(result, "records", None) or []
    return [
        {**row, "synthetic": True, "synthetic_model": "nemo-data-designer"}
        for row in rows
        if isinstance(row, dict)
    ][:count]


def run_sync(
    rows: list[dict[str, Any]], params: dict[str, Any], limit: int | None
) -> list[dict[str, Any]]:
    """Append generated rows to the stream. Called from the build worker thread.

    `limit` is the preview's row budget: a preview generates a couple of rows so
    you can see the shape, rather than the full count, because a preview that takes
    four minutes is not a preview.
    """
    import asyncio

    count = int(params.get("count") or 20)
    if limit is not None:
        count = min(count, 2)
    if count <= 0:
        return rows
    engine = str(params.get("engine") or "local")
    coro = (
        _generate_nemo(rows, params, count)
        if engine == "nemo"
        else _generate_local(rows, params, count)
    )
    try:
        # Safe from a worker thread precisely because that thread has no running
        # loop; the build never runs on the event-loop thread.
        generated = asyncio.run(coro)
    except SynthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SynthError(f"generation failed: {exc}") from exc
    return rows + generated
