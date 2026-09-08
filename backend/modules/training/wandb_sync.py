"""Reading W&B runs back into localtrack, so a remote run charts beside a local one.

The `trackers` connector has always been one-way. It holds a W&B key and injects
`WANDB_API_KEY` into the kernel environment at spawn, and that is the entire
relationship: the run goes *out*, nothing comes back, and a fine-tune you ran on a
rented box last week is invisible next to the one you ran here this morning.

This closes it. `list_runs` and `import_run` pull a run's config and metric history
through W&B's public API and write them into localtrack through the same `RunMirror`
the local training path uses — so an imported run appears in the same sidebar, on
the same charts, and in the same `compare_runs` table as a local one.

Three decisions:

**The tools live in a `wandb` group, not a `trackers` one.** The `trackers`
connector deliberately contributes no agent tools, and `test_connectors.py` enforces
that its id names no tool group — the orchestrator groups by name prefix, so a
`trackers.*` tool would silently split the connector's tools from its blurb. These
are `wandb.projects` / `wandb.runs` / `wandb.import_runs`.

They were `training.wandb_*`, which put them in the `training` group. Importing
someone else's cloud runs is not part of any local loop, and three tools is a cheap
group to skip — while `training` had grown past what a small model can hold beside
`evals` (see `test_trainer_tool_budget.py`).

**The history is downsampled by W&B, and we say so.** `run.history()` returns a
sampled series by default; `scan_history` is exact and far slower. The default is
sampled, the sample size is reported on the imported run, and a curve that is 500
points where the original had 40,000 must not be presented as the original.

**An import is idempotent by name.** Re-importing the same W&B run updates the same
localtrack row rather than adding a second, because the interesting reason to
re-import is "it has run further since".
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Points pulled per run by default. W&B's own sampling; enough to draw a curve,
#: few enough that importing a project of twenty runs is not a download.
DEFAULT_SAMPLES = 500


class WandbError(RuntimeError):
    """W&B could not be reached or the key was rejected."""


def _api() -> Any:
    """An authenticated W&B API client, from the `trackers` connector's key."""
    try:
        import wandb  # noqa: PLC0415 — optional; see the error below
    except ImportError as exc:
        raise WandbError(
            "the `wandb` package is not installed in the backend environment. "
            "Run `uv add wandb` to enable importing runs; training runs that "
            "*write* to W&B need it only in the project venv, not here."
        ) from exc

    from backend.modules.training import trackers

    key = trackers.wandb_key()
    if not key:
        raise WandbError(
            "no Weights & Biases API key is connected. Connect the Experiment "
            "trackers tile on the home page."
        )
    try:
        return wandb.Api(api_key=key)
    except Exception as exc:  # noqa: BLE001 — vendor errors shown verbatim
        raise WandbError(f"W&B rejected the key: {exc}") from exc


def list_projects(entity: str = "") -> list[dict[str, Any]]:
    """Projects this key can see. `entity` defaults to the key's own account."""
    api = _api()
    try:
        owner = entity or api.default_entity
        projects = api.projects(owner)
        return [
            {"entity": owner, "name": p.name, "path": f"{owner}/{p.name}"}
            for p in projects
        ]
    except Exception as exc:  # noqa: BLE001
        raise WandbError(f"could not list W&B projects: {exc}") from exc


def list_runs(project: str, limit: int = 50) -> list[dict[str, Any]]:
    """Runs in `entity/project`, newest first."""
    api = _api()
    path = project if "/" in project else f"{api.default_entity}/{project}"
    try:
        runs = api.runs(path, order="-created_at")
    except Exception as exc:  # noqa: BLE001
        raise WandbError(f"could not list runs in {path!r}: {exc}") from exc

    out: list[dict[str, Any]] = []
    for run in runs:
        if len(out) >= limit:
            break
        out.append(
            {
                "id": run.id,
                "path": "/".join(run.path),
                "name": run.name,
                "state": run.state,
                "createdAt": str(getattr(run, "created_at", "")),
                # The config, minus W&B's own bookkeeping keys, which are not
                # hyperparameters and would clutter every comparison table.
                "config": {
                    k: v for k, v in (run.config or {}).items() if not k.startswith("_")
                },
                "summary": {
                    k: v
                    for k, v in dict(run.summary or {}).items()
                    if isinstance(v, (int, float)) and not k.startswith("_")
                },
            }
        )
    return out


def import_run(
    run_path: str,
    *,
    project: str = "wandb",
    samples: int = DEFAULT_SAMPLES,
    exact: bool = False,
) -> dict[str, Any]:
    """Pull one W&B run into localtrack. Returns what landed.

    `exact=True` uses `scan_history`, which returns every logged step and is much
    slower. The default is W&B's sampled history, and the result says which was
    used — a 500-point curve that stood in for 40,000 must not be presented as the
    original.
    """
    api = _api()
    try:
        run = api.run(run_path)
    except Exception as exc:  # noqa: BLE001
        raise WandbError(f"could not open W&B run {run_path!r}: {exc}") from exc

    from backend.modules.localtrack.mirror import RunMirror

    config = {k: v for k, v in (run.config or {}).items() if not k.startswith("_")}
    mirror = RunMirror(
        project,
        name=run.name or run.id,
        # `_source` and `_wandb` are read by the comparison table, which strips
        # underscore-prefixed keys from the varied columns — they are provenance,
        # not hyperparameters.
        config={**config, "_source": "wandb", "_wandb": run_path},
        tags=["wandb", *(run.tags or [])],
        # Deterministic id: re-importing a run that has advanced updates the same
        # row rather than adding a second one beside it.
        run_id=f"wandb-{run.id}",
    )
    if not mirror.active:
        raise WandbError(f"could not open a localtrack run: {mirror.error}")

    rows = 0
    try:
        history = (
            run.scan_history() if exact else run.history(samples=samples, pandas=False)
        )
        for row in history:
            if not isinstance(row, dict):
                continue
            step = row.get("_step")
            values = {
                k: float(v)
                for k, v in row.items()
                if not k.startswith("_")
                and isinstance(v, (int, float))
                and not isinstance(v, bool)
            }
            if not values:
                continue
            mirror.log(int(step or rows), values)
            rows += 1
    except Exception as exc:  # noqa: BLE001 — a partial import is still worth having
        logger.info("wandb: history for %s ended early (%s)", run_path, exc)

    summary = {
        k: float(v)
        for k, v in dict(run.summary or {}).items()
        if isinstance(v, (int, float))
        and not isinstance(v, bool)
        and not k.startswith("_")
    }
    # W&B's `state` vocabulary is not localtrack's: `finished` matches, everything
    # else that is not running is a failure of some kind.
    status = "finished" if run.state == "finished" else "failed"
    mirror.finish(status if run.state != "running" else "finished", summary=summary)

    return {
        "runId": mirror.run_id,
        "project": project,
        "name": run.name or run.id,
        "points": rows,
        "exact": bool(exact),
        "note": (
            ""
            if exact
            else f"W&B returned a sampled history (up to {samples} points); pass "
            "exact=true for every logged step."
        ),
    }
