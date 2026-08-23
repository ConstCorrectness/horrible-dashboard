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
    try:
        # `is None`, never falsiness: an explicit 0 means pure CPU and has to
        # survive being passed through.
        gpu_layers = args.get("gpuLayers")
        llama_manager.spawn(
            model_path,
            gpu_layers=tuning.gpu_layers if gpu_layers is None else int(gpu_layers),
            threads=tuning.threads,
        )
    except RuntimeError as exc:
        return {"error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"error": f"bad argument: {exc}"}

    ready = await llama_manager.wait_ready()
    payload = _status_payload()
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
        },
        required=["modelPath"],
        side_effect=True,
        specifier_template="{modelPath}",
        handler=_serve,
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
]


def register_llamacpp_tools() -> None:
    from backend.sdk.registry import registry

    for tool in LLAMACPP_TOOLS:
        registry.agent_tools[tool.name] = tool
