"""Agent tools for the datasets module.

The half of "build me a dataset from the eval failures and fine-tune on it" that
the agent could not previously do at all. Grouped under `datasets`, which names no
connector — the sources carry their own credentials (the Hub connector, the Kaggle
settings), so this group is about *material*, not about an account.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.datasets import builder, formats, registry, sources, tokens
from backend.modules.datasets.models import PipelineModel
from backend.sdk.registry import registry as plugin_registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


async def _peek_rows(
    source_id: str, ref: str, config: str, split: str, limit: int
) -> tuple[list[str], list[dict[str, Any]]]:
    found = sources.get_source(source_id)
    return await asyncio.to_thread(found.peek, ref, config, split, limit)


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    source = str(args.get("source") or "hub")
    try:
        found = sources.get_source(source)
        refs = await asyncio.to_thread(
            found.search, str(args.get("query") or ""), int(args.get("limit") or 15)
        )
    except sources.SourceError as exc:
        return {"error": str(exc)}
    return {
        "source": source,
        "datasets": [r.model_dump(exclude={"meta"}) for r in refs],
    }


async def _peek(args: dict[str, Any]) -> dict[str, Any]:
    """Columns, rows and a format verdict — the tool that stops the agent guessing
    a `text_field` the way the form used to make the user guess one."""
    try:
        columns, rows = await _peek_rows(
            str(args.get("source") or "hub"),
            str(args.get("ref") or ""),
            str(args.get("config") or ""),
            str(args.get("split") or "train"),
            max(1, min(int(args.get("limit") or 3), 20)),
        )
    except sources.SourceError as exc:
        return {"error": str(exc)}
    detection = formats.detect(columns, rows)
    return {
        "columns": columns,
        "rows": rows,
        "detection": detection.to_dict(),
        "usableBy": [
            task
            for task, allowed in formats.TASK_FORMATS.items()
            if detection.format in allowed
        ],
    }


async def _splits(args: dict[str, Any]) -> dict[str, Any]:
    try:
        found = sources.get_source(str(args.get("source") or "hub"))
        rows = await asyncio.to_thread(found.splits, str(args.get("ref") or ""))
    except sources.SourceError as exc:
        return {"error": str(exc)}
    return {"splits": rows}


async def _adapt(args: dict[str, Any]) -> dict[str, Any]:
    task = str(args.get("task") or "sft")
    dataset_id = str(args.get("dataset_id") or "")
    if dataset_id:
        found = registry.get(dataset_id)
        if found is None:
            return {"error": f"no registered dataset {dataset_id!r}"}
        source, ref, config, split = found.source, found.ref, found.config, found.split
    else:
        source = str(args.get("source") or "hub")
        ref = str(args.get("ref") or "")
        config = str(args.get("config") or "")
        split = str(args.get("split") or "train")
    try:
        columns, rows = await _peek_rows(source, ref, config, split, 5)
    except sources.SourceError as exc:
        return {"error": str(exc)}
    detection = formats.detect(columns, rows)
    adaptation = formats.adapt(detection, task)
    return {
        "task": task,
        "detection": detection.to_dict(),
        "adaptation": adaptation.to_dict(),
        "textField": formats.text_field_for(detection.format, adaptation.columns),
    }


async def _register(args: dict[str, Any]) -> dict[str, Any]:
    source = str(args.get("source") or "hub")
    ref = str(args.get("ref") or "")
    if not ref:
        return {"error": "ref is required"}
    config = str(args.get("config") or "")
    split = str(args.get("split") or "train")
    try:
        columns, rows = await _peek_rows(source, ref, config, split, 5)
    except sources.SourceError as exc:
        columns, rows = [], []
        logger.info("datasets: registering %s unpeeked (%s)", ref, exc)
    detection = formats.detect(columns, rows)
    path = ""
    try:
        path = await asyncio.to_thread(sources.get_source(source).locate, ref)
    except sources.SourceError:
        path = ""
    saved = await asyncio.to_thread(
        registry.register,
        name=str(args.get("name") or "") or ref,
        source=source,
        ref=ref,
        config=config,
        split=split,
        fmt=detection.format,
        column_map=detection.columns,
        path=path,
        notes=str(args.get("notes") or ""),
    )
    return {"dataset": saved.model_dump(), "detection": detection.to_dict()}


async def _list(args: dict[str, Any]) -> dict[str, Any]:
    found = await asyncio.to_thread(
        registry.list_datasets, str(args.get("source") or "")
    )
    return {"datasets": [d.model_dump() for d in found]}


async def _token_stats(args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(args.get("dataset_id") or "")
    saved = registry.get(dataset_id) if dataset_id else None
    if dataset_id and saved is None:
        return {"error": f"no registered dataset {dataset_id!r}"}
    source = saved.source if saved else str(args.get("source") or "hub")
    ref = saved.ref if saved else str(args.get("ref") or "")
    config = saved.config if saved else str(args.get("config") or "")
    split = saved.split if saved else str(args.get("split") or "train")
    try:
        columns, rows = await _peek_rows(source, ref, config, split, 200)
    except sources.SourceError as exc:
        return {"error": str(exc)}
    detection = formats.detect(columns, rows)
    stats = await tokens.token_stats(
        rows,
        fmt=saved.format if saved else detection.format,
        columns=(saved.column_map if saved else detection.columns),
        model=str(args.get("model") or ""),
        max_length=int(args.get("max_length") or 1024),
    )
    return stats.model_dump()


async def _build(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("pipeline")
    if not isinstance(raw, dict):
        return {"error": "pipeline must be an object with a `steps` array"}
    try:
        pipeline = PipelineModel.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — a shape error is the agent's to fix
        return {"error": f"pipeline is malformed: {exc}"}
    problems = builder.validate(pipeline)
    if problems:
        return {"error": "; ".join(problems), "problems": problems}
    if args.get("preview"):
        return (await asyncio.to_thread(builder.preview, pipeline, 5)).model_dump()
    try:
        build_id = builder.start_build(
            pipeline, str(args.get("name") or ""), bool(args.get("register", True))
        )
    except builder.BuildError as exc:
        return {"error": str(exc)}
    return {"buildId": build_id, "status": "started"}


async def _build_status(args: dict[str, Any]) -> dict[str, Any]:
    return {"builds": builder.build_status(str(args.get("build_id") or ""))}


_SOURCE_PARAM = {
    "type": "string",
    "description": "Source id: hub (Hugging Face), local, exports (evals & "
    "trajectories output), kaggle. Defaults to hub.",
}
_REF_PARAM = {
    "type": "string",
    "description": "Dataset id for a remote source (e.g. 'trl-lib/Capybara'), or a "
    "path relative to the source's directory for a local one.",
}

TOOLS: list[AgentTool] = [
    AgentTool(
        name="datasets.search",
        description="Find datasets on the Hugging Face Hub, on Kaggle, in local "
        "files, or among the SFT exports evals and trajectories have written.",
        handler=_search,
        parameters={
            "query": {"type": "string", "description": "Search text"},
            "source": _SOURCE_PARAM,
            "limit": {"type": "integer", "description": "Max results (default 15)"},
        },
        required=["query"],
        group="datasets",
    ),
    AgentTool(
        name="datasets.peek",
        description="Read real columns and rows from a dataset without downloading "
        "it, and report which shape it is in (chatml/sharegpt/alpaca/preference/"
        "raw_text) and which training tasks can use it. Call this before pointing a "
        "recipe at a dataset.",
        handler=_peek,
        parameters={
            "ref": _REF_PARAM,
            "source": _SOURCE_PARAM,
            "config": {"type": "string", "description": "Hub config name, if any"},
            "split": {"type": "string", "description": "Split (default 'train')"},
            "limit": {"type": "integer", "description": "Rows to read (default 3)"},
        },
        required=["ref"],
        group="datasets",
    ),
    AgentTool(
        name="datasets.splits",
        description="List the (config, split) pairs a dataset offers.",
        handler=_splits,
        parameters={"ref": _REF_PARAM, "source": _SOURCE_PARAM},
        required=["ref"],
        group="datasets",
    ),
    AgentTool(
        name="datasets.adapt",
        description="Answer whether a given training task (sft, dpo, kto, reward, "
        "grpo, cpt, pretrain) can train on this dataset, with the column map it "
        "would need. Refuses with a reason when the shapes are incompatible.",
        handler=_adapt,
        parameters={
            "task": {"type": "string", "description": "Training task, e.g. 'sft'"},
            "dataset_id": {
                "type": "string",
                "description": "A registered dataset's id (preferred), instead of ref+source",
            },
            "ref": _REF_PARAM,
            "source": _SOURCE_PARAM,
            "config": {"type": "string", "description": "Hub config name, if any"},
            "split": {"type": "string", "description": "Split (default 'train')"},
        },
        required=["task"],
        group="datasets",
    ),
    AgentTool(
        name="datasets.register",
        description="Save a dataset definition so a recipe can point at it by id. "
        "Detects and records its format and column map, which is what makes a "
        "later run reproducible.",
        handler=_register,
        parameters={
            "ref": _REF_PARAM,
            "source": _SOURCE_PARAM,
            "name": {"type": "string", "description": "Display name"},
            "config": {"type": "string", "description": "Hub config name, if any"},
            "split": {"type": "string", "description": "Split (default 'train')"},
            "notes": {"type": "string", "description": "Free-text note"},
        },
        required=["ref"],
        side_effect=True,
        specifier_template="register dataset {ref}",
        group="datasets",
    ),
    AgentTool(
        name="datasets.list",
        description="List the datasets registered on this node.",
        handler=_list,
        parameters={"source": _SOURCE_PARAM},
        required=[],
        group="datasets",
    ),
    AgentTool(
        name="datasets.token_stats",
        description="Token-length statistics over a sample of a dataset, including "
        "the fraction of examples that would be TRUNCATED at a given max_length. "
        "Truncation is silent during training, so check this before a run.",
        handler=_token_stats,
        parameters={
            "dataset_id": {
                "type": "string",
                "description": "A registered dataset's id",
            },
            "ref": _REF_PARAM,
            "source": _SOURCE_PARAM,
            "config": {"type": "string", "description": "Hub config name, if any"},
            "split": {"type": "string", "description": "Split (default 'train')"},
            "model": {
                "type": "string",
                "description": "Base model, so the count uses its real tokenizer "
                "rather than a characters/4 estimate",
            },
            "max_length": {
                "type": "integer",
                "description": "Sequence length to measure against (default 1024)",
            },
        },
        required=[],
        group="datasets",
    ),
    AgentTool(
        name="datasets.build",
        description="Build a new dataset from a pipeline of steps (load, filter, "
        "template, dedupe, sample, synthesize, split). Pass preview=true to run it "
        "over a few rows and see what each step does before committing.",
        handler=_build,
        parameters={
            "pipeline": {
                "type": "object",
                "description": "{name, steps: [{op, params, enabled}]}. Ops: load "
                "{source, ref, datasetId, config, split}, filter {column, test: "
                "contains|matches|longer|shorter|nonempty, value, negate}, template "
                "{template, output}, dedupe {column}, sample {count, seed}, "
                "synthesize {prompt, count, engine}, split {fraction, column}.",
            },
            "name": {"type": "string", "description": "Name for the built dataset"},
            "preview": {
                "type": "boolean",
                "description": "Run over a few rows and return them instead of building",
            },
            "register": {
                "type": "boolean",
                "description": "Register the result (default true)",
            },
        },
        required=["pipeline"],
        side_effect=True,
        specifier_template="build dataset {name}",
        group="datasets",
    ),
    AgentTool(
        name="datasets.build_status",
        description="Progress of dataset builds, per step.",
        handler=_build_status,
        parameters={"build_id": {"type": "string", "description": "One build's id"}},
        required=[],
        group="datasets",
    ),
]


def register_agent_tools() -> None:
    for tool in TOOLS:
        plugin_registry.agent_tools[tool.name] = tool
