"""The `llamacpp` agent tool group: list the node's GGUFs, and serve one.

This module had **no agent tools at all**, which made the fine-tuning agent's own
prompt a description of a loop it could not run. `roster.TRAINER_PROMPT` tells it to
convert a checkpoint and score the result, and `training.convert` now writes the
GGUF — but "serve it" was a button in a pane and nothing else, so the last step
before an eval always needed a human.

Note the trap this group inverts. `docs/modules/training.mdx` and `roster.py` both
recorded, correctly, that `llamacpp` must **not** appear in an agent's
`tool_groups`: a group is a tool name's prefix (`_group_of`), the `llamacpp.*`
namespace held only *settings* keys, and naming it would have granted nothing at
all, silently. That reasoning was right up to the moment these tools existed, and
is wrong after it — both comments are updated alongside this file.

Deliberately not exposed: installing or removing a build, and deleting a model.
Fetching a multi-gigabyte binary or erasing weights on a tool call is a decision
that belongs to a person looking at a disk-budget readout, not to a turn that has
inferred it would be helpful.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


def _status_payload() -> dict[str, Any]:
    from backend.modules.llamacpp.server import llama_manager

    raw = llama_manager.status()
    # A deliberate subset. `status()` also carries the install list and a log ring;
    # an agent has nowhere to put those and would pay for them in context on every
    # call. `ready` is separate from `running` and both matter: a process that is up
    # but still loading weights answers nothing yet.
    return {
        "installed": bool(raw.get("installed")),
        "running": bool(raw.get("running")),
        "ready": bool(raw.get("ready")),
        "modelPath": raw.get("modelPath") or "",
        # `status()` spells the alias `model` — it is what llama-server advertises
        # and therefore the id a client asks for.
        "model": raw.get("model") or "",
        "endpoint": raw.get("endpoint") or "",
        "error": raw.get("error") or "",
    }


async def _status(_args: dict[str, Any]) -> Any:
    return _status_payload()


async def _list_models(_args: dict[str, Any]) -> Any:
    """Every GGUF this node can serve, with provenance where it has any.

    Covers the managed directory *and* the files Ollama and LM Studio already have
    — those are serveable in place and never copied, so a model the user downloaded
    in another app is listed here rather than being invisible.
    """
    from backend.modules.llamacpp import catalog
    from backend.modules.training import lineage

    models = await asyncio.to_thread(catalog.list_models)
    provenance = lineage.by_path()
    out: list[dict[str, Any]] = []
    for model in models:
        path = str(model.path)
        origin = provenance.get(path) or {}
        out.append(
            {
                "name": model.name,
                "path": path,
                "origin": model.origin,
                "sizeBytes": model.size_bytes,
                "architecture": model.architecture,
                # Present only for a model this node trained. Absent is the normal
                # case (anything downloaded), and must read as "unknown provenance"
                # rather than as a claim about the base.
                "baseModel": origin.get("baseModel") or None,
                "projectId": origin.get("projectId") or None,
            }
        )
    return {"models": out, "serving": _status_payload()}


def tuning_vram() -> int | None:
    """VRAM for the offload arithmetic, or None when we could not ask.

    None rather than a default: `spec_plan` refuses to plan without a real figure,
    and a guessed one is an out-of-memory error at load time rather than a
    diagnosable refusal.
    """
    from backend.modules.hardware import probe as hardware

    primary = hardware.get_profile().primary
    return primary.vram_mb if primary else None


async def _find_drafts(args: dict[str, Any]) -> Any:
    """Which local GGUFs could serve as a draft for a target model.

    Compatibility is read from the file headers, never inferred from names: two
    files whose names share a prefix routinely have different tokenizers, and such
    a pair does not fail at load — it collapses the acceptance rate and makes
    generation slower while looking like it worked.
    """
    from backend.modules.llamacpp import features, speculative

    model_path = str(args.get("modelPath", "")).strip()
    if not model_path:
        return {"error": "modelPath is required; call llamacpp.list_models first"}

    info = features.probe_flags()
    candidates = speculative.find_drafts(model_path)
    return {
        "target": model_path,
        "drafts": candidates,
        # Reported even when candidates exist: a build that cannot draft makes
        # every one of them unusable, and saying so here saves a failed spawn.
        "buildSupportsSpeculative": info.speculative,
        "buildReason": info.draft_model.reason,
    }


async def _serve(args: dict[str, Any]) -> Any:
    """Load a GGUF into the local llama-server.

    `llama-server` holds **one model at a time** and `spawn` refuses while a server
    is running, so serving a second model means stopping the first. That is stated
    in the error rather than done implicitly: the running server may be the user's
    chat provider, and swapping it out from under them because a turn wanted to
    score something else is not a decision a tool should make.
    """
    from backend.modules.hardware import probe as hardware
    from backend.modules.llamacpp.server import llama_manager

    model_path = str(args.get("modelPath", "")).strip()
    if not model_path:
        return {"error": "modelPath is required; call llamacpp.list_models first"}

    current = _status_payload()
    if current["running"]:
        if current["modelPath"] == model_path:
            # Already the right weights: not an error, and not a reload either.
            return {"alreadyServing": True, **current}
        return {
            "error": (
                "a server is already running with a different model "
                f"({current['modelPath']}). Call llamacpp.stop first if replacing it "
                "is what you want — it may be the model the user is chatting with."
            ),
            **current,
        }

    tuning = hardware.defaults()
    # `is None`, never falsiness: an explicit 0 means pure CPU and has to survive
    # being passed through.
    gpu_layers = args.get("gpuLayers")
    gpu_layers = tuning.gpu_layers if gpu_layers is None else int(gpu_layers)
    extra_args: list[str] = []
    spec_note = ""

    draft_path = str(args.get("draftModelPath", "") or "").strip()
    if draft_path:
        # Deliberately NOT a free-form `extraArgs` passthrough. A raw arg list is
        # how an agent bricks a server load with a flag this build does not have,
        # and the failure surfaces only as "did not start".
        from backend.modules.llamacpp import features, speculative

        verdict = speculative.check_compatible(model_path, draft_path)
        if not verdict["compatible"]:
            # Refused, not warned. A mismatched tokenizer does not error at load —
            # the acceptance rate collapses and generation gets *slower* while
            # looking like it worked, which is the one failure nobody would catch.
            return {"error": f"that draft model is incompatible: {verdict['reason']}"}

        info = features.probe_flags()
        if not info.speculative:
            detail = info.draft_model.reason or "no draft-model flag"
            return {"error": f"this llama.cpp build cannot draft: {detail}"}

        plan = speculative.spec_plan(
            model_path,
            draft_path,
            vram_mb=tuning_vram(),
            context=int(args.get("contextSize") or 4096),
        )
        if plan["targetGpuLayers"] is not None:
            gpu_layers = plan["targetGpuLayers"]
        try:
            extra_args = features.speculative_args(
                draft_path,
                draft_gpu_layers=plan["draftGpuLayers"],
                features=info,
            )
        except RuntimeError as exc:
            return {"error": str(exc)}
        spec_note = plan["reason"]

    try:
        llama_manager.spawn(
            model_path,
            gpu_layers=gpu_layers,
            threads=tuning.threads,
            extra_args=extra_args,
        )
    except RuntimeError as exc:
        return {"error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"error": f"bad argument: {exc}"}

    ready = await llama_manager.wait_ready()
    payload = _status_payload()
    if spec_note:
        payload["speculative"] = {"draftModelPath": draft_path, "plan": spec_note}
    if not ready:
        # `wait_ready` returns False rather than raising when a model is too large
        # to load. Reporting success here is how a caller comes to score a model
        # that never came up and read the zero as the model's fault.
        return {
            "error": "the server did not become ready (is the model too large?)",
            **payload,
        }
    return payload


async def _stop(_args: dict[str, Any]) -> Any:
    from backend.modules.llamacpp.server import llama_manager

    llama_manager.stop()
    return _status_payload()


async def _list_traces(args: dict[str, Any]) -> Any:
    """The stored activation traces, read from the catalog rather than the disk.

    The catalog is in `app.db` and answers this in one query; walking the
    directory means parsing every manifest, which is the same answer for more
    work. `sync()` on startup keeps the two honest.
    """
    from backend.modules.llamacpp import trace_catalog, trace_runner

    available, reason = trace_runner.available()
    rows = await asyncio.to_thread(
        trace_catalog.rows,
        limit=int(args.get("limit") or 20),
        model_sha=str(args.get("modelSha") or ""),
        derived_from=str(args.get("derivedFrom") or ""),
    )
    return {
        "traces": [
            {
                "traceId": r["traceId"],
                "model": r["modelName"],
                "prompt": r["prompt"],
                "promptTokens": r["promptTokens"],
                "fidelity": r["fidelity"],
                "attention": r["attention"],
                # Present only on a fork, and the pair is the whole point of a
                # fork appearing in a listing at all.
                "derivedFrom": r["derivedFrom"] or None,
                "edits": r["edits"] or None,
                "diskBytes": r["diskBytes"],
                "createdAt": r["createdAt"],
            }
            for r in rows
        ],
        "canTrace": available,
        "reason": reason,
    }


async def _trace(args: dict[str, Any]) -> Any:
    """Run one traced forward pass and store it.

    Defaults to the **lens** capture set and no attention, which is the cheap
    shape: the residual stream plus the output head is ~1% of a full trace, so
    this returns in seconds rather than minutes and costs megabytes rather than
    a gigabyte. `full=true` opts into the architecture's whole default set for
    someone who wants the mechanism and not just the readout.
    """
    from backend.modules.hardware import probe as hardware
    from backend.modules.llamacpp import trace_runner, traces
    from backend.modules.llamacpp.routes import _architecture
    from backend.modules.llamacpp.tracer import CAPTURE_PRESETS

    model_path = str(args.get("modelPath", "")).strip()
    prompt = str(args.get("prompt", "")).strip()
    if not model_path:
        return {"error": "modelPath is required; call llamacpp.list_models first"}
    if not prompt:
        return {"error": "prompt is required — a trace runs on text"}

    available, reason = trace_runner.available()
    if not available:
        return {"error": reason}

    full = bool(args.get("full"))
    spec: dict[str, Any] = {
        "modelPath": model_path,
        "prompt": prompt,
        "capture": [] if full else list(CAPTURE_PRESETS["lens"]),
        "maxTokens": max(0, min(int(args.get("maxTokens") or 0), 16)),
        "layers": [],
        # Attention only on request *and* only with the full set: the score
        # matrix grows with the square of the token count and is the single
        # largest thing a trace can hold.
        "attention": bool(args.get("attention")) and full,
        "fidelity": "fp16",
        "tokenCap": hardware.defaults().trace_token_cap,
        "gpuLayers": None,
        "architecture": await asyncio.to_thread(_architecture, model_path),
    }

    error = ""
    trace_id = ""
    pruned: list[str] = []
    async for event in trace_runner.run_trace(spec):
        if event.get("error"):
            error = str(event["error"])
        if event.get("status") == "stored":
            trace_id = str(event.get("traceId") or "")
            pruned = list(event.get("pruned") or [])
    if error or not trace_id:
        return {"error": error or "the tracer produced no trace"}

    # `run_trace` catalogued it already — one chokepoint, not two.
    trace = traces.load(trace_id)
    manifest = trace.manifest if trace else {}
    return {
        "traceId": trace_id,
        "model": manifest.get("modelName") or "",
        "promptTokens": manifest.get("promptTokens") or 0,
        "records": manifest.get("recordCount") or 0,
        "diskBytes": trace.bytes_on_disk() if trace else 0,
        # Named rather than silent: a trace that pushed the directory over its
        # budget deleted somebody's older one, and finding that out later is
        # worse than being told.
        "pruned": pruned,
        "next": "Load the `lens` tool group to read this trace as words.",
    }


LLAMACPP_TOOLS: list[AgentTool] = [
    AgentTool(
        name="llamacpp.status",
        description="Whether this node is serving a local GGUF, and which one.",
        parameters={},
        handler=_status,
        group="llamacpp",
    ),
    AgentTool(
        name="llamacpp.list_models",
        description=(
            "Every GGUF this node can serve — its own converted fine-tunes plus any "
            "Ollama or LM Studio already has. Reports which model a fine-tune was "
            "trained from, when this node trained it."
        ),
        parameters={},
        handler=_list_models,
        group="llamacpp",
    ),
    AgentTool(
        name="llamacpp.serve",
        description=(
            "Load a GGUF into the local llama-server so it can answer requests. One "
            "model at a time: fails rather than replacing a server that is already "
            "running a different model."
        ),
        parameters={
            "modelPath": {
                "type": "string",
                "description": "Path from llamacpp.list_models.",
            },
            "gpuLayers": {
                "type": "integer",
                "description": "Layers to offload. Omit to let the hardware probe decide; 0 forces CPU.",
            },
            "draftModelPath": {
                "type": "string",
                "description": (
                    "Optional smaller model for speculative decoding. Must share "
                    "the target's tokenizer — an incompatible one is refused, "
                    "since it would silently make generation slower rather than "
                    "failing. Use llamacpp.find_drafts to get valid candidates."
                ),
            },
            "contextSize": {
                "type": "integer",
                "description": (
                    "Context length, used to size the KV cache when dividing the "
                    "card between the two models."
                ),
            },
        },
        required=["modelPath"],
        side_effect=True,
        specifier_template="{modelPath}",
        handler=_serve,
        group="llamacpp",
    ),
    AgentTool(
        name="llamacpp.find_drafts",
        description=(
            "List local GGUFs that can be used as a draft model for a target, for "
            "speculative decoding. Checks tokenizer compatibility by reading file "
            "headers, and reports whether the installed build supports drafting."
        ),
        parameters={
            "modelPath": {
                "type": "string",
                "description": "The target model, from llamacpp.list_models.",
            },
        },
        required=["modelPath"],
        handler=_find_drafts,
        group="llamacpp",
    ),
    AgentTool(
        name="llamacpp.stop",
        description="Stop the local llama-server, freeing its memory.",
        parameters={},
        side_effect=True,
        handler=_stop,
        group="llamacpp",
    ),
    AgentTool(
        name="llamacpp.list_traces",
        description=(
            "Activation traces stored on this node — recorded forward passes that "
            "the `lens` tools read as words. Shows which are forks of which."
        ),
        parameters={
            "limit": {"type": "integer", "description": "Rows to return (default 20)."},
            "modelSha": {
                "type": "string",
                "description": "Only traces of this model hash.",
            },
            "derivedFrom": {
                "type": "string",
                "description": "Only forks of this trace id.",
            },
        },
        handler=_list_traces,
        group="llamacpp",
    ),
    AgentTool(
        name="llamacpp.trace",
        description=(
            "Record a forward pass of a local GGUF over a prompt, so the `lens` "
            "tools can read what the model was disposed to say at each layer. "
            "Cheap by default (residual stream only, seconds); `full` captures the "
            "whole mechanism and is minutes and gigabytes."
        ),
        parameters={
            "modelPath": {
                "type": "string",
                "description": "Path from llamacpp.list_models.",
            },
            "prompt": {
                "type": "string",
                "description": "Traced as raw text — no chat template is applied.",
            },
            "maxTokens": {
                "type": "integer",
                "description": "Tokens to generate after the prompt (0-16, default 0).",
            },
            "full": {
                "type": "boolean",
                "description": "Capture the architecture's whole default set, not just the lens nodes.",
            },
            "attention": {
                "type": "boolean",
                "description": "Attention scores. Needs full; the largest thing in a trace.",
            },
        },
        required=["modelPath", "prompt"],
        side_effect=True,
        specifier_template="{modelPath}",
        handler=_trace,
        group="llamacpp",
    ),
]


def register_llamacpp_tools() -> None:
    from backend.modules.llamacpp.lens_tools import register_lens_tools
    from backend.sdk.registry import registry

    for tool in LLAMACPP_TOOLS:
        registry.agent_tools[tool.name] = tool
    # The `lens` group ships with this module but is its own group: reading a
    # trace as words and supervising a server are different jobs, and a turn that
    # wants one rarely wants the other's schemas.
    register_lens_tools()
