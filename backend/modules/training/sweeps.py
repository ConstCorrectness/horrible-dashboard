"""Ablations: run the same recipe N times with one thing changed.

This is the thing the training module could not do at all. A `recipe.json` was a
flat `values` dict and `apply_to_notebook` replaced its block idempotently, so
"what does learning rate actually do here" meant editing a number, running,
screenshotting the chart, editing it back, and remembering. That is not an
experiment; it is a recollection.

A sweep is the same recipe with **axes**: a field and the values to try. The
expansion is a grid (every combination), a zip (the Nth value of each axis
together, for when two knobs must move in step), or a random sample of the grid.

Four decisions do the real work:

**Runs are processes, not kernel cells.** A kernel is single and serial; twelve
points on one would take twelve times as long and could not be stopped
individually. Each point becomes a `.py` from `recipes.materialize_script` — a
*flattening* of the same `materialize` the notebook uses, so a sweep and a manual
run cannot disagree about what a recipe means — and goes through the script runner.

**The run's config IS the resolved values dict.** `localtrack` already stores
`config_json` per run and has never had a producer that filled it with anything
worth comparing. That field is the entire mechanism behind "which knob caused
this": the comparison view reads it back and diffs.

**Sequential by default, and the default is right.** Two concurrent fine-tunes on
one GPU is not two-times throughput, it is an out-of-memory error at some
unpredictable point in the second one. `max_parallel` exists for people with
several cards and defaults to 1.

**A sweep does not ride the shared task queue.** That queue is serial across the
whole app, so a twelve-point sweep would park every library ingest behind it for
an afternoon. Detached under its own semaphore, like the evals sweep and karaoke
downloads.
"""

from __future__ import annotations

import itertools
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.modules.training import recipes
from backend.modules.training.metrics import declare_run_config
from backend.modules.training.models import ProjectModel
from backend.modules.training.runners.script_runner import script_runner
from backend.modules.training.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)

STRATEGIES = ("grid", "zip", "random")

#: Where generated point scripts land, inside the project.
SCRIPT_DIR = "sweeps"

#: Points a sweep may expand to. A four-axis grid of five values each is 625 runs;
#: the cap turns "I did not think about that" into an error instead of a machine
#: that is busy until Thursday.
MAX_POINTS = 64


class SweepError(RuntimeError):
    """A sweep could not be expanded or started. Always says which axis."""


@dataclass
class Axis:
    field: str
    values: list[Any]

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "values": list(self.values)}


@dataclass
class SweepSpec:
    axes: list[Axis] = field(default_factory=list)
    strategy: str = "grid"
    max_parallel: int = 1
    #: Random strategy only: how many points to draw, and the seed that makes the
    #: draw reproducible. An unseeded sample makes a sweep unrepeatable, which
    #: defeats the point of running one.
    count: int = 8
    seed: int = 42
    note: str = ""

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SweepSpec:
        axes = []
        for raw in data.get("axes") or []:
            if not isinstance(raw, dict):
                continue
            values = raw.get("values")
            axes.append(
                Axis(
                    str(raw.get("field") or ""),
                    list(values) if isinstance(values, list) else [],
                )
            )
        return SweepSpec(
            axes=axes,
            strategy=str(data.get("strategy") or "grid"),
            max_parallel=max(1, int(data.get("maxParallel") or 1)),
            count=max(1, int(data.get("count") or 8)),
            seed=int(data.get("seed") or 42),
            note=str(data.get("note") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": [a.to_dict() for a in self.axes],
            "strategy": self.strategy,
            "maxParallel": self.max_parallel,
            "count": self.count,
            "seed": self.seed,
            "note": self.note,
        }


@dataclass
class Point:
    """One run of the sweep: the values that vary, and the recipe they produce."""

    index: int
    label: str
    overrides: dict[str, Any]
    recipe: recipes.Recipe

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "overrides": dict(self.overrides),
            "outputDir": self.recipe.output_dir,
        }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", str(text).lower()).strip("-") or "x"


def validate(base: recipes.Recipe, spec: SweepSpec) -> list[str]:
    """Everything wrong with this sweep, before anything runs."""
    problems: list[str] = []
    if spec.strategy not in STRATEGIES:
        problems.append(
            f"unknown strategy {spec.strategy!r}. Known: {', '.join(STRATEGIES)}"
        )
    if not spec.axes:
        problems.append("a sweep needs at least one axis")

    known = {f.name for f in recipes.catalog(base.backend, base.task, True)}
    for axis in spec.axes:
        if not axis.field:
            problems.append("an axis names no field")
            continue
        if axis.field not in known:
            # Naming a field this backend/task does not have would produce N runs
            # that are all identical, and a comparison view showing no effect —
            # which reads as "this knob does nothing".
            problems.append(
                f"`{axis.field}` is not a field of {base.backend}/{base.task}. "
                f"Available: {', '.join(sorted(known))}"
            )
        if not axis.values:
            problems.append(f"axis `{axis.field}` has no values")
        elif len(axis.values) == 1:
            problems.append(
                f"axis `{axis.field}` has one value, so it does not vary — a sweep "
                "over it measures nothing"
            )

    if spec.strategy == "zip" and spec.axes:
        lengths = {len(a.values) for a in spec.axes if a.values}
        if len(lengths) > 1:
            problems.append(
                "a zip sweep steps every axis together, so they must all have the "
                f"same number of values (got {sorted(lengths)})"
            )
    return problems


def expand(base: recipes.Recipe, spec: SweepSpec) -> list[Point]:
    """The concrete runs this sweep describes."""
    problems = validate(base, spec)
    if problems:
        raise SweepError("; ".join(problems))

    axes = [a for a in spec.axes if a.values]
    if spec.strategy == "zip":
        combos = [tuple(a.values[i] for a in axes) for i in range(len(axes[0].values))]
    else:
        combos = list(itertools.product(*(a.values for a in axes)))
        if spec.strategy == "random" and spec.count < len(combos):
            combos = random.Random(spec.seed).sample(combos, spec.count)

    if len(combos) > MAX_POINTS:
        raise SweepError(
            f"this sweep expands to {len(combos)} runs, over the limit of "
            f"{MAX_POINTS}. Narrow an axis or use the random strategy."
        )

    points: list[Point] = []
    for index, combo in enumerate(combos):
        overrides = {axis.field: value for axis, value in zip(axes, combo)}
        label = " ".join(f"{k}={v}" for k, v in overrides.items())
        recipe = recipes.Recipe.from_dict(base.to_dict())
        recipe.values = {**recipe.values, **overrides}
        # Each point gets its own output dir, or twelve runs overwrite one
        # checkpoint and the sweep produces exactly one model.
        recipe.output_dir = (
            f"{base.output_dir.rstrip('/')}/sweep-{index:02d}-"
            f"{_slug('-'.join(f'{k}{v}' for k, v in overrides.items()))}"
        )
        points.append(Point(index, label, overrides, recipe))
    return points


# --- execution ---------------------------------------------------------------

_sweeps: dict[str, dict[str, Any]] = {}
_stop: set[str] = set()


def status(sweep_id: str = "") -> list[dict[str, Any]]:
    if sweep_id:
        found = _sweeps.get(sweep_id)
        return [found] if found else []
    return list(_sweeps.values())


def stop(sweep_id: str) -> bool:
    """Ask a sweep to stop: kill the running point, start no more.

    The running point is killed rather than awaited, because "stop" means stop —
    but points that already finished keep their results, since a sweep abandoned
    halfway still answers the question for the points it got through.
    """
    record = _sweeps.get(sweep_id)
    if record is None or record["state"] not in ("queued", "running"):
        return False
    _stop.add(sweep_id)
    for run_id in record.get("runIds") or []:
        script_runner.stop(run_id)
    record["state"] = "stopping"
    broadcast_threadsafe("sweep_state", _public(record))
    return True


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "points"}


def start(
    project: ProjectModel,
    base: recipes.Recipe,
    spec: SweepSpec,
    intro: recipes.Introspection,
) -> str:
    """Expand the sweep, write its scripts, and run them. Returns the sweep id."""
    points = expand(base, spec)
    sweep_id = uuid.uuid4().hex[:12]
    _sweeps[sweep_id] = {
        "sweepId": sweep_id,
        "projectId": project.id,
        "state": "queued",
        "strategy": spec.strategy,
        "axes": [a.to_dict() for a in spec.axes],
        "total": len(points),
        "done": 0,
        "failed": 0,
        "runIds": [],
        "results": [],
        "points": points,
        "startedAt": time.time(),
    }
    threading.Thread(
        target=_run_sweep,
        args=(sweep_id, project, spec, intro),
        daemon=True,
        name=f"sweep-{sweep_id}",
    ).start()
    return sweep_id


def _write_script(
    project: ProjectModel, sweep_id: str, point: Point, intro: recipes.Introspection
) -> str:
    """Generate this point's script inside the project. Returns the relative path."""
    folder = Path(project.root) / SCRIPT_DIR / sweep_id
    folder.mkdir(parents=True, exist_ok=True)
    name = f"point-{point.index:02d}.py"
    run_name = point.label or f"point {point.index}"
    (folder / name).write_text(
        recipes.materialize_script(point.recipe, intro, run_name), encoding="utf-8"
    )
    return f"{SCRIPT_DIR}/{sweep_id}/{name}"


def _run_sweep(
    sweep_id: str,
    project: ProjectModel,
    spec: SweepSpec,
    intro: recipes.Introspection,
) -> None:
    record = _sweeps[sweep_id]
    points: list[Point] = record["points"]
    record["state"] = "running"
    broadcast_threadsafe("sweep_state", _public(record))

    # One at a time by default: two fine-tunes on one card is an OOM at an
    # unpredictable point in the second, not double the throughput.
    gate = threading.Semaphore(max(1, spec.max_parallel))
    threads: list[threading.Thread] = []

    def run_point(point: Point) -> None:
        with gate:
            if sweep_id in _stop:
                return
            entry: dict[str, Any] = {
                **point.to_dict(),
                "state": "running",
                "runId": "",
            }
            record["results"].append(entry)
            try:
                script = _write_script(project, sweep_id, point, intro)
                # Declare the config *before* launching, keyed by the run name the
                # script will announce. The point's own values, plus what varied
                # marked separately — a comparison view needs to know which of the
                # forty numbers were the experiment and which were the constants.
                declare_run_config(
                    project.id,
                    point.label or f"point {point.index}",
                    {
                        **point.recipe.values,
                        "_sweep": sweep_id,
                        "_axes": list(point.overrides),
                        "_backend": point.recipe.backend,
                        "_task": point.recipe.task,
                    },
                )
                run = script_runner.start(project, script)
            except Exception as exc:  # noqa: BLE001 — reported, not raised
                entry.update(state="failed", error=str(exc))
                record["failed"] += 1
                broadcast_threadsafe("sweep_point", {"sweepId": sweep_id, **entry})
                return
            entry["runId"] = run.id
            record["runIds"].append(run.id)
            broadcast_threadsafe("sweep_point", {"sweepId": sweep_id, **entry})

            # The script runner owns the process; wait on it here so the semaphore
            # actually serializes runs rather than serializing their *launches*.
            while run.running:
                time.sleep(0.5)
            ok = run.returncode == 0
            entry.update(
                state="finished" if ok else "failed",
                returncode=run.returncode,
                metricRuns=list(run.metric_runs),
            )
            if ok:
                record["done"] += 1
            else:
                record["failed"] += 1
            broadcast_threadsafe("sweep_point", {"sweepId": sweep_id, **entry})

    for point in points:
        thread = threading.Thread(
            target=run_point,
            args=(point,),
            daemon=True,
            name=f"sweep-{sweep_id}-{point.index}",
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    record["state"] = "stopped" if sweep_id in _stop else "finished"
    record["finishedAt"] = time.time()
    _stop.discard(sweep_id)
    broadcast_threadsafe("sweep_state", _public(record))
    logger.info(
        "sweep %s %s: %d done, %d failed",
        sweep_id,
        record["state"],
        record["done"],
        record["failed"],
    )


def save_spec(project: ProjectModel, spec: SweepSpec) -> None:
    """Remember the sweep alongside the recipe, so the form round-trips."""
    from backend.atomic_write import write_text_atomic

    write_text_atomic(
        Path(project.root) / "sweep.json", json.dumps(spec.to_dict(), indent=2)
    )


def load_spec(project: ProjectModel) -> SweepSpec:
    path = Path(project.root) / "sweep.json"
    if not path.is_file():
        return SweepSpec()
    try:
        return SweepSpec.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.info("training: unreadable sweep for %s (%s)", project.id, exc)
        return SweepSpec()


def reset() -> None:
    """Test hook."""
    _sweeps.clear()
    _stop.clear()
