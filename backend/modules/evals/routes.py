"""REST surface for evals, mounted at `/api/evals`.

Suites, runs and results are ordinary CRUD. The one interesting endpoint is
`POST /runs`, which **returns immediately**: a sweep takes minutes, so the request
starts it and progress arrives on the `evals` `/ws` channel. A synchronous version
of that route would hold a connection open for the length of the sweep and time out
in every proxy between here and the browser.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.evals import store, sweep
from backend.modules.evals.models import (
    EvalCase,
    EvalSuite,
    ExportRequest,
    ExportResponse,
    ResultListResponse,
    RunListResponse,
    StartRunRequest,
    SuiteListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evals", tags=["evals"])


class CreateSuiteRequest(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []


class CaseListResponse(BaseModel):
    suite: EvalSuite
    cases: list[EvalCase]
    #: Set when the file could not be parsed. The cases list is then empty, and the
    #: pane shows this instead of an empty suite — "your JSON is broken on line 12"
    #: and "this suite has no cases" must not look the same.
    error: str = ""


class StartRunResponse(BaseModel):
    started: bool
    key: str = ""
    message: str = ""


@router.get("/suites", response_model=SuiteListResponse)
async def list_suites() -> SuiteListResponse:
    return SuiteListResponse(suites=store.list_suites())


class ForkSuiteRequest(BaseModel):
    name: str = ""


@router.post("/suites", response_model=EvalSuite)
async def create_suite(req: CreateSuiteRequest) -> EvalSuite:
    """A new, empty suite.

    It used to be seeded from cases hardcoded in Python. The starter cases are now
    a bundled `.jsonl` that lists beside your own — fork it if you want a copy to
    edit, rather than having its contents silently copied into every suite you
    make.
    """
    return store.create_suite(req.name, req.description, req.tags)


@router.post("/suites/{suite_id}/fork", response_model=EvalSuite)
async def fork_suite(suite_id: str, req: ForkSuiteRequest) -> EvalSuite:
    """Copy a suite (usually a bundled one) into a new suite you own and can edit."""
    try:
        return store.fork_suite(suite_id, req.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/suites/{suite_id}/cases", response_model=CaseListResponse)
async def get_cases(suite_id: str) -> CaseListResponse:
    suite = store.get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"no suite {suite_id!r}")
    try:
        cases = store.load_cases(suite)
    except store.SuiteFormatError as exc:
        # 200 with an error, not a 4xx: the suite exists and the pane needs to
        # render it *with* the parse error, so you can open the file and fix line
        # 12. A 422 would leave the pane with nothing to show but a toast.
        return CaseListResponse(suite=suite, cases=[], error=str(exc))
    return CaseListResponse(suite=suite, cases=cases)


@router.put("/suites/{suite_id}/cases", response_model=CaseListResponse)
async def put_cases(suite_id: str, cases: list[EvalCase]) -> CaseListResponse:
    suite = store.get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"no suite {suite_id!r}")
    try:
        store.write_cases(suite, cases)
    except store.ReadOnlySuiteError as exc:
        # 409, not 403: nothing is wrong with your credentials, this suite is the
        # wrong *kind* of suite to write to, and the fix is to fork it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CaseListResponse(suite=store.get_suite(suite_id) or suite, cases=cases)


@router.delete("/suites/{suite_id}")
async def delete_suite(suite_id: str, remove_file: bool = False) -> dict[str, Any]:
    try:
        return {"deleted": store.delete_suite(suite_id, remove_file=remove_file)}
    except store.ReadOnlySuiteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/exports", response_model=ExportResponse)
async def export_dataset(req: ExportRequest) -> ExportResponse:
    """Turn a run's results into a supervised fine-tuning dataset on disk.

    The tool catalog is taken from a live browser for the same reason a sweep needs
    one: an example has to carry the tool schemas the case was graded against, and
    those live only on a connected socket. Unlike a sweep this does not refuse
    without one — a `no_call`-heavy export is still useful with an empty catalog,
    and the count of skipped cases makes the gap visible rather than silent.
    """
    from backend.modules.evals import export

    try:
        return export.build(
            req.run_id,
            mode=req.mode,
            reference_run_id=req.reference_run_id,
            agent_tools=_live_agent_tools(),
            out=req.out,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/exports/preview")
async def preview_dataset(
    run_id: str, mode: str = "both", limit: int = 3
) -> dict[str, Any]:
    """The first few examples, without writing a file — what you check before
    training on a dataset is what one row looks like."""
    from backend.modules.evals import export

    try:
        return {
            "examples": export.preview(
                run_id, limit=limit, mode=mode, agent_tools=_live_agent_tools()
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --- authoring a benchmark block --------------------------------------------
#
# Every wrong benchmark case so far has been wrong in the same way: it named a
# column the dataset does not have, or graded against a field holding the worked
# solution rather than the answer. Both score zero and both look exactly like a bad
# model. These three endpoints exist so the form can be right by construction
# rather than by guesswork.


class PeekRequest(BaseModel):
    dataset: str
    config: str = ""
    split: str = "train"
    limit: int = 3


class ComparePreviewRequest(BaseModel):
    """What a case would compare, for one real row."""

    row: dict[str, Any]
    input_template: str = "{question}"
    target_column: str = "answer"
    target_regex: str = ""
    prediction_regex: str = ""
    #: A reply to test `prediction_regex` against. Typed by the user, because the
    #: alternative is calling the model from a form field.
    sample_prediction: str = ""


@router.get("/datasets/splits")
async def dataset_splits(dataset: str) -> dict[str, Any]:
    """The (config, split) pairs a Hub dataset offers.

    `config` is the field people miss — `gsm8k` has no default one, and omitting it
    fails in a way that reads as "the dataset is broken".
    """
    from backend.modules.evals import datasets

    try:
        return {"splits": await datasets.splits(dataset)}
    except datasets.PeekError as exc:
        # 502, not 422: nothing is wrong with the request, the Hub did not answer.
        # The form degrades to free-text fields on this, so it must be
        # distinguishable from "you asked for something impossible".
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/datasets/peek")
async def dataset_peek(req: PeekRequest) -> dict[str, Any]:
    """Column names and a few real rows, without downloading the dataset."""
    from backend.modules.evals import datasets

    try:
        return await datasets.first_rows(req.dataset, req.config, req.split, req.limit)
    except datasets.PeekError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/datasets/compare-preview")
async def compare_preview(req: ComparePreviewRequest) -> dict[str, Any]:
    """Run the case's extraction over one row and report what would be compared.

    Uses the *same* `extract`/`normalise` the generated harness uses — imported,
    not reimplemented — so the preview cannot flatter the case.
    """
    from backend.modules.evals import datasets

    return datasets.compare_preview(
        req.row,
        input_template=req.input_template,
        target_column=req.target_column,
        target_regex=req.target_regex,
        prediction_regex=req.prediction_regex,
        sample_prediction=req.sample_prediction,
    )


@router.get("/datasets/presets")
async def dataset_presets() -> dict[str, Any]:
    """Known-good benchmark blocks for datasets whose answer column is a trap.

    This is the GSM8K lesson written down. Its answer column holds the worked
    solution and marks the final answer with `####`, so a case that grades the
    column as-is scores zero against a model that is answering perfectly. A preset
    is cheaper than a paragraph nobody reads at the moment they need it.
    """
    from backend.modules.evals.presets import PRESETS

    return {"presets": PRESETS}


@router.get("/leaderboard")
async def get_leaderboard(
    suite_id: str, run_ids: str = "", limit: int = 8
) -> dict[str, Any]:
    """Compare a suite's runs: the ranking, the case matrix, and what nobody passes.

    `run_ids` is a comma-separated subset; blank means the newest `limit` finished
    runs. Only finished runs appear — a half-complete sweep would sit in the table
    looking like a model that failed everything it has not reached yet.
    """
    from backend.modules.evals import leaderboard

    try:
        return leaderboard.build(
            suite_id,
            [r for r in run_ids.split(",") if r.strip()] or None,
            limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/leaderboard/diff")
async def get_diff(base: str, other: str) -> dict[str, Any]:
    """What one run fixed and broke relative to another, over the cases both ran."""
    from backend.modules.evals import leaderboard

    try:
        return leaderboard.diff(base, other)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs", response_model=RunListResponse)
async def list_runs(suite_id: str = "", limit: int = 100) -> RunListResponse:
    return RunListResponse(runs=store.list_runs(suite_id or None, limit))


@router.get("/runs/{run_id}", response_model=ResultListResponse)
async def get_run(run_id: str) -> ResultListResponse:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    return ResultListResponse(run=run, results=store.list_results(run_id))


@router.post("/runs", response_model=StartRunResponse)
async def start_run(req: StartRunRequest) -> StartRunResponse:
    """Start a sweep. Returns as soon as it is queued."""
    if not req.targets:
        raise HTTPException(status_code=422, detail="a sweep needs at least one target")

    # The tool catalog the cases are graded against. Taken from a live browser
    # connection, because that is where the manifest lives — the frontend pushes
    # its panes' `agentTools` onto the socket, and there is no server-side copy.
    #
    # This is a real constraint and the error says so plainly: a sweep with no
    # browser attached would silently grade every model against a catalog of
    # backend tools only, and score them all at zero on anything UI-shaped.
    tools = _live_agent_tools()
    if not tools:
        return StartRunResponse(
            started=False,
            message=(
                "No browser is connected, so the frontend tool catalog is empty. "
                "Open the dashboard in a window and start the sweep again."
            ),
        )

    try:
        key = sweep.start_sweep(
            req.suite_id,
            req.targets,
            tools,
            req.case_ids or None,
            req.localtrack_project,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StartRunResponse(started=True, key=key)


@router.get("/sweeps")
async def list_sweeps() -> dict[str, Any]:
    """The sweeps running on this node right now.

    A sweep outlives the pane that started it, so "what is running" cannot be
    answered from the pane's own state — the same reason progress broadcasts
    rather than replies.
    """
    return {"sweeps": sweep.active_sweeps()}


@router.delete("/sweeps/{key:path}")
async def cancel_sweep(key: str) -> dict[str, Any]:
    """Stop a sweep. Runs it had already finished keep their results.

    `{key:path}` because a sweep key contains a colon; the default converter would
    still match, but being explicit stops a later key format from silently 404ing.
    """
    return {"cancelled": sweep.cancel_sweep(key)}


def _live_agent_tools() -> list[dict[str, Any]]:
    """The richest tool manifest any connected browser has pushed.

    A one-line forward to `agent.offline_conn.live_agent_tools`, which is where the
    helper moved when agentpedia's fork needed the same answer. The name stays
    because several call sites in this module use it.
    """
    from backend.modules.agent.offline_conn import live_agent_tools

    return live_agent_tools()


@router.get("/targets")
async def suggest_targets() -> dict[str, Any]:
    """Models this node could sweep, so the launcher is a picker rather than a form.

    Three sources, and they are genuinely different things: the configured agent
    provider (what the node uses today), any llama.cpp builds installed locally
    (what you fine-tuned and converted), and the roster's per-agent overrides.
    """
    out: list[dict[str, Any]] = []
    try:
        from backend.modules.agent.roster import resolve_provider
        from backend.modules.agent.routes import _load_config

        config = _load_config()
        if config is not None:
            info, endpoint = resolve_provider(config, "main")
            out.append(
                {
                    "provider": info.kind,
                    "endpoint": endpoint,
                    "model": config.model,
                    "label": f"{config.model} (this node)",
                    "source": "agent",
                }
            )
    except Exception:  # noqa: BLE001
        logger.debug(
            "evals: could not resolve the node's agent provider", exc_info=True
        )

    # The local GGUFs, which is what "score the checkpoint I just converted" means.
    # These used to be the llama.cpp *builds* (`list_installs`), emitted with an
    # empty `model` — a build is a binary, not something you can evaluate, so the
    # one target that mattered on this node was the one you could not pick.
    try:
        from backend.modules.llamacpp import catalog
        from backend.modules.llamacpp.server import llama_manager

        from backend.modules.training import lineage as _lineage

        loaded = llama_manager.model_path
        models = catalog.list_models()
        # One query for the whole catalog rather than one per file: this route
        # lists every GGUF on the machine, and a lookup per row would be a query
        # per model on every page load.
        provenance = _lineage.by_path()
        # Managed first: those are the ones this node produced, and after a
        # fine-tune the file you want is the one you just wrote.
        models.sort(key=lambda m: (m.origin != "managed", m.name.lower()))
        for model in models:
            path = str(model.path)
            is_loaded = bool(loaded) and _same_path(loaded, path)
            # Where this file came from, when this node made it. Absent for every
            # model the user downloaded, which is the normal case and must render
            # as "no provenance" rather than as a guess.
            origin = provenance.get(path)
            label = f"{model.name} ({model.origin})"
            if origin and origin.get("baseModel"):
                label += f" — fine-tune of {origin['baseModel']}"
            if is_loaded:
                label += " — loaded"
            out.append(
                {
                    "provider": "llamacpp",
                    # Left blank deliberately: the sweep loads the GGUF and reads
                    # the endpoint after the spawn, because the port is chosen then.
                    "endpoint": "",
                    # llama-server advertises the `--alias`, which defaults to the
                    # file stem — so this is the id it will answer to.
                    "model": model.path.stem,
                    "modelPath": path,
                    "label": label,
                    "source": "llamacpp",
                    "loaded": is_loaded,
                    # The whole of "score my fine-tune against its base": the
                    # picker can offer both targets in one click because this says
                    # what the base was. Null for anything this node did not train.
                    "baseModel": (origin or {}).get("baseModel") or None,
                    "projectId": (origin or {}).get("projectId") or None,
                    "isAdapter": bool((origin or {}).get("isAdapter")),
                    # Shown in the picker rather than used to filter it. The
                    # catalog reads this from the GGUF header, so an embedder
                    # (`nomic-bert`) or a TTS model sitting in the same directory
                    # is visibly not a chat model — but a header we could not
                    # parse is not evidence of anything, and hiding a model on
                    # that basis would be presenting a guess as a measurement.
                    "architecture": model.architecture,
                }
            )
    except Exception:  # noqa: BLE001
        logger.debug("evals: llama.cpp catalog unavailable", exc_info=True)

    return {"targets": out}


def _same_path(a: str, b: str) -> bool:
    from pathlib import Path

    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b
