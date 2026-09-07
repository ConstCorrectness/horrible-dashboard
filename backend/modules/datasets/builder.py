"""Building a dataset from other datasets: a pipeline of typed steps.

The shape is Unsloth Studio's "Data Recipes" and the reason is the same: the useful
training set is almost never the one you downloaded. It is that one filtered to the
rows that are long enough, deduplicated, templated into chat turns, mixed with a few
hundred rows of your own failures, and split. Every one of those is three lines of
`datasets` code, and writing them by hand in a notebook means the dataset that
trained a model exists only as cells someone edited afterwards.

**Validate, then preview, then build** — the split that makes a node editor usable
rather than a slower way to write the same code:

- `validate()` answers "is this pipeline coherent" without touching data. A `filter`
  on a column no upstream step produces is caught here, in milliseconds.
- `preview()` runs the whole pipeline over the first N rows and reports **per-step
  row counts**. A filter that drops everything produces empty output either way; the
  counts are what tell you *which* step did it.
- `build()` runs it for real, detached, writing `.jsonl`.

Two decisions worth stating:

**Steps are a closed vocabulary, not arbitrary code.** A `map` step taking a Python
lambda would be more powerful and would also make a pipeline an arbitrary-code
document that the agent can author and a plugin can install. The steps here are
declarative; anything they cannot express belongs in the notebook, where it is
visible.

**Builds do not ride the shared task queue.** That queue is serial, and a build over
a large corpus would park every library ingest behind it. Detached under a
semaphore, like `karaoke` downloads and the evals sweep, for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backend.modules.datasets import registry, sources
from backend.modules.datasets.models import (
    PipelineModel,
    PreviewModel,
)
from backend.modules.datasets.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)

#: The closed vocabulary. See the module docstring for why it is closed.
OPS = ("load", "filter", "template", "dedupe", "sample", "synthesize", "split", "write")

#: Concurrent builds. Small on purpose: a build is IO- and sometimes GPU-bound, and
#: three at once on a laptop is three that are all slow.
_MAX_CONCURRENT = 2
_semaphore = threading.Semaphore(_MAX_CONCURRENT)


def built_root() -> Path:
    root = sources.data_root() / "built"
    root.mkdir(parents=True, exist_ok=True)
    return root


class BuildError(RuntimeError):
    """A pipeline could not run. Names the step, always."""


# --- validation --------------------------------------------------------------


def validate(pipeline: PipelineModel) -> list[str]:
    """Everything wrong with this pipeline, without touching a row.

    Returns problems rather than raising: a builder pane wants to show all of them
    at once, and stopping at the first means fixing them one round-trip each.
    """
    problems: list[str] = []
    steps = [s for s in pipeline.steps if s.enabled]
    if not steps:
        return ["the pipeline has no enabled steps"]

    loads = [s for s in steps if s.op == "load"]
    if not loads:
        problems.append("no `load` step — a pipeline has to start from some data")
    elif steps[0].op != "load":
        problems.append("the first enabled step must be a `load`")

    for index, step in enumerate(steps):
        label = f"step {index + 1} (`{step.op}`)"
        if step.op not in OPS:
            problems.append(f"{label}: unknown operation. Known: {', '.join(OPS)}")
            continue
        params = step.params or {}
        if step.op == "load":
            if not params.get("ref"):
                problems.append(f"{label}: names no dataset")
            if index and loads.index(step) == 0:
                pass
        elif step.op == "filter":
            if not params.get("column"):
                problems.append(f"{label}: names no column")
            if params.get("test") not in (
                "contains",
                "matches",
                "longer",
                "shorter",
                "nonempty",
            ):
                problems.append(
                    f"{label}: test must be one of contains/matches/longer/shorter/nonempty"
                )
            if params.get("test") == "matches":
                try:
                    re.compile(str(params.get("value") or ""))
                except re.error as exc:
                    problems.append(f"{label}: invalid regex — {exc}")
        elif step.op == "template":
            if not params.get("output"):
                problems.append(f"{label}: names no output column")
            if not params.get("template"):
                problems.append(f"{label}: has an empty template")
        elif step.op == "dedupe":
            if not params.get("column"):
                problems.append(f"{label}: names no column to dedupe on")
        elif step.op == "split":
            fraction = params.get("fraction", 0.1)
            if not isinstance(fraction, (int, float)) or not 0 < float(fraction) < 1:
                problems.append(f"{label}: fraction must be between 0 and 1")
        elif step.op == "synthesize" and not params.get("prompt"):
            problems.append(f"{label}: has no generation prompt")

    # Column reachability. The check that earns the validate pass its keep: a
    # `filter` on `answer` when nothing upstream produces `answer` is a pipeline
    # that yields zero rows and blames the data.
    produced: set[str] = set()
    known_columns = False
    for index, step in enumerate(steps):
        params = step.params or {}
        if step.op == "load":
            columns = params.get("columns")
            if isinstance(columns, list) and columns:
                produced |= {str(c) for c in columns}
                known_columns = True
        elif step.op == "template":
            produced.add(str(params.get("output") or ""))
        elif step.op in ("filter", "dedupe") and known_columns:
            column = str(params.get("column") or "")
            if column and column not in produced:
                problems.append(
                    f"step {index + 1} (`{step.op}`) reads `{column}`, which no "
                    f"earlier step produces. Available: {', '.join(sorted(produced))}"
                )
    return problems


# --- execution ---------------------------------------------------------------


def _apply_filter(
    rows: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    column = str(params.get("column") or "")
    test = str(params.get("test") or "nonempty")
    value = params.get("value")
    negate = bool(params.get("negate"))

    def keep(row: dict[str, Any]) -> bool:
        cell = row.get(column)
        text = cell if isinstance(cell, str) else json.dumps(cell, default=str)
        if test == "nonempty":
            result = bool(text and text.strip())
        elif test == "contains":
            result = str(value or "").lower() in text.lower()
        elif test == "matches":
            result = re.search(str(value or ""), text) is not None
        elif test == "longer":
            result = len(text) > int(value or 0)
        elif test == "shorter":
            result = len(text) < int(value or 0)
        else:
            result = True
        return result != negate

    return [r for r in rows if keep(r)]


def _apply_template(
    rows: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    template = str(params.get("template") or "")
    output = str(params.get("output") or "text")
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            rendered = template.format(**row)
        except (KeyError, IndexError, ValueError) as exc:
            # Named as a step failure rather than skipped: a template naming a
            # column that isn't there would otherwise silently produce a dataset
            # of empty strings.
            raise BuildError(
                f"template names {exc}, which the rows do not have. "
                f"Columns are: {', '.join(rows[0])}"
            ) from exc
        out.append({**row, output: rendered})
    return out


def _apply_dedupe(
    rows: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    column = str(params.get("column") or "")
    normalize = bool(params.get("normalize", True))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        cell = row.get(column)
        text = (
            cell
            if isinstance(cell, str)
            else json.dumps(cell, default=str, sort_keys=True)
        )
        if normalize:
            text = " ".join(text.lower().split())
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _apply_sample(
    rows: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    count = int(params.get("count") or 0)
    if count <= 0 or count >= len(rows):
        return rows
    # Seeded, so a preview and the build that follows it agree about which rows
    # were picked. An unseeded sample makes the preview a different dataset.
    rng = random.Random(int(params.get("seed", 42)))
    return rng.sample(rows, count)


def _apply_split(
    rows: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Tag rows with a split rather than returning two lists.

    One stream keeps every downstream step simple, and `load_dataset` can filter on
    the column. A pipeline that returned a pair would need every later step to
    handle both halves — which is how a dedupe silently runs on train only.
    """
    fraction = float(params.get("fraction", 0.1))
    column = str(params.get("column") or "split")
    rng = random.Random(int(params.get("seed", 42)))
    indices = list(range(len(rows)))
    rng.shuffle(indices)
    holdout = set(indices[: max(1, int(len(rows) * fraction))]) if rows else set()
    return [
        {**row, column: "test" if index in holdout else "train"}
        for index, row in enumerate(rows)
    ]


def _apply_load(params: dict[str, Any], limit: int | None) -> list[dict[str, Any]]:
    source_id = str(params.get("source") or "hub")
    ref = str(params.get("ref") or "")
    dataset_id = str(params.get("datasetId") or "")
    if dataset_id:
        registered = registry.get(dataset_id)
        if registered is None:
            raise BuildError(f"no registered dataset {dataset_id!r}")
        source_id, ref = registered.source, registered.ref
        params = {**params, "config": registered.config, "split": registered.split}
    if not ref:
        raise BuildError("load names no dataset")
    source = sources.get_source(source_id)
    try:
        _, rows = source.peek(
            ref,
            str(params.get("config") or ""),
            str(params.get("split") or "train"),
            limit if limit is not None else int(params.get("limit") or 1000),
        )
    except sources.SourceError as exc:
        raise BuildError(str(exc)) from exc
    return rows


def _run_steps(
    pipeline: PipelineModel,
    *,
    limit: int | None,
    on_step: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute the pipeline; returns (rows, per-step report)."""
    rows: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    for index, step in enumerate(pipeline.steps):
        if not step.enabled:
            report.append(
                {"index": index, "op": step.op, "skipped": True, "rows": len(rows)}
            )
            continue
        before = len(rows)
        started = time.monotonic()
        params = step.params or {}
        if step.op == "load":
            loaded = _apply_load(params, limit)
            # A second load appends: mixing your eval failures into a public
            # dataset is the motivating case for the whole builder.
            rows = rows + loaded if rows else loaded
        elif step.op == "filter":
            rows = _apply_filter(rows, params)
        elif step.op == "template":
            rows = _apply_template(rows, params)
        elif step.op == "dedupe":
            rows = _apply_dedupe(rows, params)
        elif step.op == "sample":
            rows = _apply_sample(rows, params)
        elif step.op == "split":
            rows = _apply_split(rows, params)
        elif step.op == "synthesize":
            from backend.modules.datasets import synth  # noqa: PLC0415 — optional path

            rows = synth.run_sync(rows, params, limit)
        elif step.op == "write":
            pass  # handled by build(); a preview never writes
        else:
            raise BuildError(f"unknown operation {step.op!r}")
        entry = {
            "index": index,
            "op": step.op,
            "rowsIn": before,
            "rows": len(rows),
            "ms": round((time.monotonic() - started) * 1000),
        }
        # The number that makes a node editor debuggable. A step that empties the
        # stream is invisible in the output and obvious here.
        if before and not len(rows):
            entry["warning"] = "this step removed every row"
        report.append(entry)
        if on_step is not None:
            on_step(entry)
    return rows, report


def preview(pipeline: PipelineModel, limit: int = 10) -> PreviewModel:
    """Run the pipeline over a small sample and report what each step did."""
    problems = validate(pipeline)
    if problems:
        return PreviewModel(problems=problems)
    try:
        # Load a little more than asked: filters and dedupes remove rows, and a
        # preview that asked for 10 and loaded 10 usually shows two.
        rows, report = _run_steps(pipeline, limit=max(limit * 5, 50))
    except (BuildError, sources.SourceError) as exc:
        return PreviewModel(problems=[str(exc)])
    columns: list[str] = []
    for row in rows[:limit]:
        for key in row:
            if key not in columns:
                columns.append(key)
    return PreviewModel(rows=rows[:limit], columns=columns, steps=report)


# --- builds ------------------------------------------------------------------

_builds: dict[str, dict[str, Any]] = {}


def build_status(build_id: str = "") -> list[dict[str, Any]]:
    if build_id:
        found = _builds.get(build_id)
        return [found] if found else []
    return list(_builds.values())


def start_build(pipeline: PipelineModel, name: str = "", save: bool = True) -> str:
    """Kick off a build on a worker thread; returns its id immediately."""
    problems = validate(pipeline)
    if problems:
        raise BuildError("; ".join(problems))
    build_id = uuid.uuid4().hex[:12]
    _builds[build_id] = {
        "buildId": build_id,
        "name": name or pipeline.name or "dataset",
        "state": "queued",
        "rows": 0,
        "steps": [],
        "startedAt": time.time(),
    }
    threading.Thread(
        target=_run_build,
        args=(build_id, pipeline, name, save),
        daemon=True,
        name=f"dataset-build-{build_id}",
    ).start()
    return build_id


def _run_build(
    build_id: str, pipeline: PipelineModel, name: str, save: bool
) -> None:
    record = _builds[build_id]
    with _semaphore:
        record["state"] = "running"
        broadcast_threadsafe("build_state", dict(record))
        try:

            def on_step(entry: dict[str, Any]) -> None:
                record["steps"].append(entry)
                record["rows"] = entry["rows"]
                broadcast_threadsafe("build_step", {"buildId": build_id, **entry})

            rows, report = _run_steps(pipeline, limit=None, on_step=on_step)
            safe = re.sub(
                r"[^a-z0-9._-]+", "-", (name or pipeline.name or "dataset").lower()
            )
            path = built_root() / f"{safe.strip('-') or 'dataset'}-{build_id}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(
                        json.dumps(row, ensure_ascii=False, default=str) + "\n"
                    )
            record.update(
                state="finished", rows=len(rows), steps=report, path=str(path)
            )
            if save:
                from backend.modules.datasets import formats  # noqa: PLC0415

                columns: list[str] = []
                for row in rows[:20]:
                    for key in row:
                        if key not in columns:
                            columns.append(key)
                detection = formats.detect(columns, rows[:20])
                saved = registry.register(
                    name=name or pipeline.name or path.stem,
                    source="local",
                    ref=str(path.relative_to(sources.data_root())).replace("\\", "/"),
                    fmt=detection.format,
                    column_map=detection.columns,
                    rows=len(rows),
                    path=str(path),
                    notes=f"built by pipeline {pipeline.name or build_id}",
                )
                record["datasetId"] = saved.id
        except Exception as exc:  # noqa: BLE001 — reported, never raised into the thread
            logger.info("dataset build %s failed: %s", build_id, exc)
            record.update(state="failed", error=str(exc))
        finally:
            record["finishedAt"] = time.time()
            broadcast_threadsafe("build_state", dict(record))
