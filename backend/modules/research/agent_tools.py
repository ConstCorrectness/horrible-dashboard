"""Backend agent tools for the research module (grouped under `research.`).

These run server-side, so they work with no browser tab attached — the agent can
capture a page, file a PDF, or launch a durable deep-research run even when the
human isn't looking. `research.start` is fire-and-forget by design: the run
outlives the chat turn, so the tool returns the run id and the agent (or the
human, via the console pane) polls `research.status` / fetches
`research.report` later.
"""

from __future__ import annotations

from typing import Any

from backend.modules.artifacts.store import artifact_path
from backend.modules.research import runstore, service
from backend.modules.research.broadcast import publish_run
from backend.modules.settings.routes import get_value
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool


async def _capture(args: dict[str, Any]) -> dict[str, Any]:
    result = await service.capture_url(
        str(args.get("url", "")),
        library=str(args.get("library") or "default"),
        tags=list(args.get("tags") or []),
    )
    source = result["source"]
    return {
        "artifact_id": result["artifact"]["id"],
        "source_id": source["id"],
        "title": source["title"],
        "status": source["status"],
    }


async def _save_pdf(args: dict[str, Any]) -> dict[str, Any]:
    result = await service.save_pdf_url(
        str(args.get("url", "")),
        library=str(args.get("library") or "default"),
        tags=list(args.get("tags") or []),
    )
    source = result["source"]
    return {
        "artifact_id": result["artifact"]["id"],
        "source_id": source["id"],
        "title": source["title"],
        "status": source["status"],
    }


async def _start_run(args: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.research.runner import research_runner

    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "research.start needs a query"}
    effort = str(args.get("effort") or "auto")
    if effort not in ("auto", "quick", "standard", "deep"):
        return {"error": f"effort must be auto|quick|standard|deep, not {effort!r}"}
    run = runstore.create_run(
        query=query,
        effort=effort,
        library=str(args.get("library") or "default"),
        token_budget=int(get_value("research.tokenBudget", 200_000) or 200_000),
    )
    research_runner.enqueue(run["id"])
    publish_run(run)
    return {
        "run_id": run["id"],
        "status": run["status"],
        "note": (
            "run started; it continues in the background and survives restarts. "
            "Check research.status later, or tell the user to watch the "
            "Deep Research console."
        ),
    }


async def _run_status(args: dict[str, Any]) -> dict[str, Any]:
    run_id = str(args.get("run_id", "")).strip()
    if run_id:
        run = runstore.get_run(run_id)
        if run is None:
            return {"error": "run not found"}
        runs = [run]
    else:
        runs = runstore.list_runs(limit=5)
    return {
        "runs": [
            {
                "run_id": r["id"],
                "query": r["query"],
                "status": r["status"],
                "effort": r["effort"],
                "error": r["error"],
                "tokens_used": r["tokens_used"],
                "report_source_id": r["report_source_id"],
            }
            for r in runs
        ]
    }


async def _run_report(args: dict[str, Any]) -> dict[str, Any]:
    run_id = str(args.get("run_id", "")).strip()
    run = runstore.get_run(run_id) if run_id else None
    if run is None:
        return {"error": "run not found — call research.status first"}
    if not run["report_artifact_id"]:
        return {"error": f"run is {run['status']} — no report yet"}
    path = artifact_path(run["report_artifact_id"])
    if path is None or not path.is_file():
        return {"error": "report artifact missing"}
    return {
        "run_id": run_id,
        "report": path.read_text(encoding="utf-8")[:40_000],
        "report_source_id": run["report_source_id"],
    }


_TOOLS = [
    AgentTool(
        name="research.start",
        description=(
            "Start a durable deep-research run: a multi-agent investigation that "
            "searches arXiv/web/library in parallel and produces a cited markdown "
            "report filed into the library. Runs take minutes and survive "
            "restarts — return the run_id to the user and check research.status "
            "later rather than waiting."
        ),
        handler=_start_run,
        parameters={
            "query": {"type": "string", "description": "The research question"},
            "effort": {
                "type": "string",
                "enum": ["auto", "quick", "standard", "deep"],
                "description": "How much work to spend (default auto)",
            },
            "library": {
                "type": "string",
                "description": "Library for evidence + report",
            },
        },
        required=["query"],
        side_effect=True,
        specifier_template="{query}",
    ),
    AgentTool(
        name="research.status",
        description=(
            "Status of deep-research runs (most recent first, or one run_id): "
            "state, token usage, error, and the report source when finished."
        ),
        handler=_run_status,
        parameters={
            "run_id": {"type": "string", "description": "Optional specific run"}
        },
        required=[],
    ),
    AgentTool(
        name="research.report",
        description="Fetch a finished run's report markdown by run_id.",
        handler=_run_report,
        parameters={"run_id": {"type": "string"}},
        required=["run_id"],
    ),
    AgentTool(
        name="research.capture",
        description=(
            "Save a web page as a self-contained HTML archive in the artifact "
            "store and file it into a knowledge library (ingested for search). "
            "Server-side fetch — works without the browser pane."
        ),
        handler=_capture,
        parameters={
            "url": {"type": "string", "description": "Page URL to capture"},
            "library": {
                "type": "string",
                "description": "Target library (default: default)",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=["url"],
        side_effect=True,
        specifier_template="{url}",
    ),
    AgentTool(
        name="research.savePdf",
        description=(
            "Download a PDF by URL into the artifact store and file it into a "
            "knowledge library (text extracted and ingested for search)."
        ),
        handler=_save_pdf,
        parameters={
            "url": {"type": "string", "description": "PDF URL"},
            "library": {
                "type": "string",
                "description": "Target library (default: default)",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=["url"],
        side_effect=True,
        specifier_template="{url}",
    ),
]


def register_research_tools() -> None:
    """Insert the research backend tools into the sdk registry (called from
    app.py, like training/games/symdex — first-party consumers of the plugin
    surface)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
