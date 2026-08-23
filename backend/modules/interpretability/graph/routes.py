"""HTTP surface for the model designer, mounted under `/api/interpretability/graph`.

Kept in its own router rather than appended to the module's `routes.py` because the
two halves answer different questions: that one reports on a model that exists, this
one edits one that does not yet. They share a pane and nothing else.

The split between the *stateless* endpoints (`/code`, `/validate`) and the stored
ones is deliberate. The canvas re-generates and re-validates on every edit, many
times a second — those calls must not touch the disk, and must not require the
design to have a name yet.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.modules.interpretability.graph import (
    codegen,
    examples,
    handoff,
    importer,
    parse,
    probe,
    shapes,
    spec,
    store,
    tracer,
)
from backend.modules.interpretability.graph.models import (
    CodeResult,
    DesignGraph,
    ShapeReport,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interpretability/graph", tags=["interpretability"])


class CatalogResponse(BaseModel):
    """Everything the palette needs to render itself, in one call."""

    nodes: list[dict] = Field(default_factory=list)
    templates: list[dict] = Field(default_factory=list)


class SaveRequest(BaseModel):
    graph: DesignGraph
    layout: store.Layout | None = None


class DesignSummary(BaseModel):
    name: str
    modified: float
    bytes: int


class DesignListResponse(BaseModel):
    designs: list[DesignSummary] = Field(default_factory=list)


@router.get("/specs", response_model=CatalogResponse)
def get_specs() -> CatalogResponse:
    """The node catalog and the starting templates.

    Served rather than duplicated in TypeScript, for the same reason the hassault
    module serves its weapon numbers and its `plane_order`: two copies of a
    vocabulary drift, and the drift is silent until a node emits code for params it
    no longer has.
    """
    return CatalogResponse(
        nodes=spec.catalog(),
        templates=[
            {"id": key, "label": label, "description": description}
            for key, (label, description, _build) in examples.TEMPLATES.items()
        ],
    )


@router.post("/code", response_model=CodeResult)
def to_code(graph: DesignGraph) -> CodeResult:
    """Graph → Python, without saving anything. The live code pane calls this."""
    return codegen.generate(graph)


class ParseRequest(BaseModel):
    source: str


class ParseResponse(BaseModel):
    """The graph a file describes, and everything we could not read.

    `error` is carried in a 200 rather than raised as a 400 on purpose: an
    unreadable file is a normal thing to have on screen mid-edit, and the pane needs
    to show the reason *beside the code you are still typing* rather than as a failed
    request. When `error` is set, `graph` is absent and the caller keeps what it has.
    """

    graph: DesignGraph | None = None
    #: Classes preserved verbatim as `custom.module` nodes. Named, because an opaque
    #: import the reader is not told about is indistinguishable from a wrong one.
    opaque: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


@router.post("/parse", response_model=ParseResponse)
def parse_source(body: ParseRequest) -> ParseResponse:
    """Python → a design graph. The half of the round trip that reads your edits."""
    try:
        result = parse.parse_module(body.source)
    except parse.ParseError as exc:
        return ParseResponse(error=str(exc))
    return ParseResponse(
        graph=result.graph, opaque=result.opaque, warnings=result.warnings
    )


@router.post("/validate", response_model=ShapeReport)
def validate(graph: DesignGraph) -> ShapeReport:
    """Tier-1 shape inference: instant, symbolic, and explicitly not torch's answer."""
    return shapes.infer(graph)


class ProbeRequest(BaseModel):
    graph: DesignGraph
    #: A training project id. Empty means "we could not ask" — reported as such
    #: rather than silently skipped, which would read as the model being fine.
    project: str = ""


@router.post("/probe", response_model=probe.ProbeResult)
def run_probe(body: ProbeRequest) -> probe.ProbeResult:
    """Tier 2: build the module in a project's venv and run a real forward pass.

    Slow (a cold torch import dominates) and deliberately manual — this is the button
    you press when you want the measurement, not something that fires as you type.
    """
    from backend.modules.training.projects import get_project

    project = get_project(body.project) if body.project else None
    if body.project and project is None:
        raise HTTPException(
            status_code=404, detail=f"No training project {body.project!r}"
        )
    return probe.run(body.graph, project)


class TraceRequest(BaseModel):
    project: str
    #: `package.module.ClassName` inside the project, importable from its root.
    target: str


@router.post("/from-traced", response_model=tracer.TraceResult)
def from_traced(body: TraceRequest) -> tracer.TraceResult:
    """The second importer: trace a real `nn.Module` into an editable design.

    Reports how much of the trace mapped onto node types, and names the classes that
    became placeholders — which raise rather than pass their input through, so a
    half-understood import cannot be trained by mistake.
    """
    from backend.modules.training.projects import get_project

    project = get_project(body.project) if body.project else None
    if body.project and project is None:
        raise HTTPException(
            status_code=404, detail=f"No training project {body.project!r}"
        )
    return tracer.trace(project, body.target)


class HandoffRequest(BaseModel):
    graph: DesignGraph
    project: str


@router.post("/handoff", response_model=handoff.HandoffResult)
def to_training(body: HandoffRequest) -> handoff.HandoffResult:
    """Write the design into a training project as `model.py` plus a notebook block.

    No new execution path: the project's own kernel, venv and Kaggle/Colab push all
    apply unchanged. The block carries its own marker so regenerating a *recipe*
    cannot delete the model, and vice versa.
    """
    from backend.modules.training.projects import get_project

    project = get_project(body.project)
    if project is None:
        raise HTTPException(
            status_code=404, detail=f"No training project {body.project!r}"
        )
    return handoff.apply(body.graph, project)


@router.post("/from-model", response_model=importer.ImportResult)
async def from_model() -> importer.ImportResult:
    """The bridge: the model the Inspect tab is showing, as an editable design.

    It deliberately re-reads the architecture through the **same** function the
    inspect route serves, rather than accepting one the browser posts back. The two
    tabs must never be able to disagree about what the model is, and a caller that
    can supply the architecture is a caller that can supply a different one.

    Stateless like `/template`: this returns a graph, and the pane saves it through
    the ordinary PUT if the user keeps it. An import that wrote itself to disk would
    overwrite whatever design was open.
    """
    from backend.modules.interpretability.routes import model_architecture

    return importer.from_architecture(await model_architecture())


@router.get("", response_model=DesignListResponse)
def list_designs() -> DesignListResponse:
    return DesignListResponse(designs=[DesignSummary(**row) for row in store.listing()])  # type: ignore[arg-type]


@router.get("/template/{template_id}", response_model=DesignGraph)
def get_template(template_id: str) -> DesignGraph:
    graph = examples.template(template_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"No template {template_id!r}")
    return graph


@router.get("/{name}", response_model=store.StoredDesign)
def get_design(name: str) -> store.StoredDesign:
    try:
        design = store.load(name)
    except store.NameError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if design is None:
        raise HTTPException(status_code=404, detail=f"No saved design {name!r}")
    return design


@router.put("/{name}", response_model=store.StoredDesign)
def put_design(name: str, body: SaveRequest) -> store.StoredDesign:
    """Save the graph and regenerate its module.

    A graph that cannot currently be turned into code is still saved, with the
    reason in `codeError`: refusing the write would lose an afternoon's work over
    one unfinished wire, and a canvas is unfinished nearly all the time.
    """
    try:
        return store.save(name, body.graph, body.layout)
    except store.NameError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.warning("could not save model-graph %s: %s", name, exc)
        raise HTTPException(
            status_code=500, detail=f"Could not save {name!r}: {exc}"
        ) from exc


@router.delete("/{name}")
def delete_design(name: str) -> dict[str, bool]:
    try:
        return {"deleted": store.delete(name)}
    except store.NameError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
