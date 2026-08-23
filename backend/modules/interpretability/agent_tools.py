"""Agent tools for the model designer.

Six verbs, one group, and the shape of the set is a decision rather than an
oversight. The obvious design — `add_node`, `connect`, `delete_node` — gives an agent
that cannot see the canvas a way to build a graph one wire at a time, which is both
the slowest possible way to describe a transformer and the easiest way to produce a
disconnected one. Every ungrouped tool is also charged to every turn of every agent
(see the orchestrator's `TOOL_BUDGET`), so a verb has to earn its slot.

What an agent is actually good at here is the *parametric* question — "what would this
be at half the width", "how much of Llama 3.2 is the embedding", "import what I am
looking at and tell me if the counts agree" — and the errand at the end of it, handing
the result to a training project. So the set is: list, inspect, import, retune, emit,
hand off. Node surgery stays where it belongs, on the canvas, where the person doing
it can see the wires.

Every tool reports our own arithmetic as an estimate, never as a measurement — the
backend has no torch, and the only thing that can turn an estimate into a measurement
is `graph/probe.py` running in a project venv.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.interpretability.graph import (
    codegen,
    handoff,
    importer,
    shapes,
    store,
)
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

#: Past this the tool result stops being context and starts being a file dump. The
#: agent gets the head and is told the real length, so it can ask for the rest by
#: reading the design's `.py` if it genuinely needs it.
MAX_SOURCE_CHARS = 6000


def _load(name: str) -> store.StoredDesign | None:
    try:
        return store.load(name)
    except store.NameError_:
        return None


def _summarise(design: store.StoredDesign) -> dict[str, Any]:
    """The shape of a design, in the terms an agent can reason about."""
    graph = design.graph
    report = shapes.infer(graph)
    counts: dict[str, int] = {}
    for node in graph.nodes:
        counts[node.type] = counts.get(node.type, 0) + 1
    return {
        "name": design.name,
        "className": graph.name,
        "config": graph.config,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "rootNodeTypes": counts,
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "nodes": len(group.nodes),
                "stackedBy": [
                    node.params.get("count", 1)
                    for node in graph.nodes
                    if node.type == "group" and node.params.get("group") == group.id
                ],
            }
            for group in graph.groups
        ],
        "estimatedParams": report.totalParams,
        "estimated": True,
        "problems": [
            {"node": issue.nodeId, "severity": issue.severity, "message": issue.message}
            for issue in report.issues
        ],
        "codeError": design.codeError,
    }


async def _list_designs(_args: dict[str, Any]) -> dict[str, Any]:
    return {"designs": store.listing()}


async def _inspect_design(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    design = _load(name)
    if design is None:
        return {"error": f"No saved design {name!r}", "designs": store.listing()}
    return _summarise(design)


async def _import_model(args: dict[str, Any]) -> dict[str, Any]:
    """Fork the model the interpretability pane is inspecting into a design.

    Returns the qualifications — what was assumed, what could not be read, whether
    our parameter count agrees with the one the weights state — because a caller
    that only sees "imported: true" will go on to describe the result as if it were
    the model, which it is only approximately.
    """
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "name is required — the design is saved under it"}
    try:
        store.check_name(name)
    except store.NameError_ as exc:
        return {"error": str(exc)}

    from backend.modules.interpretability.routes import model_architecture

    result = importer.from_architecture(await model_architecture())
    if result.graph is None:
        return {
            "imported": False,
            "error": result.error,
            "missing": result.missing,
            "model": result.model,
        }

    design = store.save(name, result.graph)
    return {
        "imported": True,
        "name": name,
        "model": result.model,
        "source": result.source,
        "assumed": result.assumed,
        "notes": result.notes,
        "statedParams": result.statedParams,
        "estimatedParams": result.estimatedParams,
        "summary": _summarise(design),
    }


async def _set_config(args: dict[str, Any]) -> dict[str, Any]:
    """Retune a design's hyperparameters and report what it cost.

    This is the whole reason the generated class keeps config values as *variables*
    rather than freezing them in: one graph describes a family of models, and moving
    between members of that family is a question worth being able to ask in words.
    """
    name = str(args.get("name") or "").strip()
    values = args.get("values")
    if not name:
        return {"error": "name is required"}
    if not isinstance(values, dict) or not values:
        return {"error": "values must be an object of config key → number"}

    design = _load(name)
    if design is None:
        return {"error": f"No saved design {name!r}"}

    graph = design.graph
    unknown = [key for key in values if key not in graph.config]
    if unknown:
        return {
            "error": (
                f"{name!r} has no config {', '.join(sorted(unknown))}. A new key would "
                "be read by no node, so setting it would change nothing."
            ),
            "config": graph.config,
        }

    before = shapes.infer(graph).totalParams
    updated = graph.model_copy(update={"config": {**graph.config, **values}})
    report = shapes.infer(updated)
    errors = [i for i in report.issues if i.severity == "error"]
    if errors:
        # Not saved. A config that makes the graph un-runnable is a change the caller
        # asked for by mistake, and writing it would also rewrite the `.py` beside it.
        return {
            "applied": False,
            "error": "That configuration does not describe a runnable model.",
            "problems": [{"node": i.nodeId, "message": i.message} for i in errors],
            "config": graph.config,
        }

    stored = store.save(name, updated, design.layout)
    return {
        "applied": True,
        "name": name,
        "config": stored.graph.config,
        "estimatedParamsBefore": before,
        "estimatedParams": report.totalParams,
        "estimated": True,
        "codeError": stored.codeError,
    }


async def _generate_code(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    design = _load(name)
    if design is None:
        return {"error": f"No saved design {name!r}"}

    result = codegen.generate(design.graph)
    if result.error:
        return {"error": result.error, "name": name}
    source = result.source
    return {
        "name": name,
        "path": str(store.paths_for(name)[0]),
        "lines": source.count("\n") + 1,
        "truncated": len(source) > MAX_SOURCE_CHARS,
        "source": source[:MAX_SOURCE_CHARS],
    }


async def _to_training_project(args: dict[str, Any]) -> dict[str, Any]:
    """Hand a finished design to a training project as a trainable model."""
    name = str(args.get("name") or "").strip()
    project_id = str(args.get("project") or "").strip()
    if not name or not project_id:
        return {"error": "name and project are both required"}

    design = _load(name)
    if design is None:
        return {"error": f"No saved design {name!r}"}

    from backend.modules.training.projects import get_project, list_projects

    project = get_project(project_id)
    if project is None:
        return {
            "error": f"No training project {project_id!r}",
            "projects": [{"id": p.id, "name": p.name} for p in list_projects()],
        }

    result = handoff.apply(design.graph, project)
    return result.model_dump()


_TOOLS = [
    AgentTool(
        name="model.list_designs",
        description="List the model designs saved in the interpretability pane's designer.",
        handler=_list_designs,
        parameters={},
        required=[],
        group="model",
    ),
    AgentTool(
        name="model.inspect_design",
        description=(
            "Read a saved model design: its hyperparameters, its blocks and how deep "
            "they stack, the estimated parameter count, and any shape problems."
        ),
        handler=_inspect_design,
        parameters={
            "name": {"type": "string", "description": "Name of the saved design"},
        },
        required=["name"],
        group="model",
    ),
    AgentTool(
        name="model.import_model",
        description=(
            "Fork the model currently being inspected (its GGUF/config metadata) into "
            "an editable design, saved under a name. Reports what had to be assumed "
            "and whether the derived parameter count agrees with the stated one."
        ),
        handler=_import_model,
        parameters={
            "name": {
                "type": "string",
                "description": "Name to save the imported design under",
            },
        },
        required=["name"],
        group="model",
    ),
    AgentTool(
        name="model.set_config",
        description=(
            "Change a saved design's hyperparameters (d_model, n_heads, n_layers, "
            "ffn_hidden, vocab_size…) and report the parameter count before and after. "
            "Refuses a configuration that would not describe a runnable model."
        ),
        handler=_set_config,
        parameters={
            "name": {"type": "string", "description": "Name of the saved design"},
            "values": {
                "type": "object",
                "description": 'Config keys to set, e.g. {"d_model": 1024, "n_layers": 24}',
            },
        },
        required=["name", "values"],
        group="model",
    ),
    AgentTool(
        name="model.to_training_project",
        description=(
            "Hand a saved design to a training project: writes it as model.py and adds "
            "a notebook block that imports and instantiates it. Regenerating replaces "
            "that block rather than adding a second one."
        ),
        handler=_to_training_project,
        parameters={
            "name": {"type": "string", "description": "Name of the saved design"},
            "project": {"type": "string", "description": "Training project id"},
        },
        required=["name", "project"],
        group="model",
    ),
    AgentTool(
        name="model.generate_code",
        description=(
            "Get the PyTorch nn.Module source a saved design generates, and the path "
            "of the .py file it was written to."
        ),
        handler=_generate_code,
        parameters={
            "name": {"type": "string", "description": "Name of the saved design"},
        },
        required=["name"],
        group="model",
    ),
]


def register_agent_tools() -> None:
    """Insert the designer's tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
