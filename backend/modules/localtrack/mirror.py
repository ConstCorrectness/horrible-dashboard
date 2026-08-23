"""Report a run into localtrack, or do nothing at all.

Two producers want the same four moves — open a project, open a run, log metrics at
a step, close it with a summary — and both want every one of them to be
unconditionally safe. localtrack is a *reporting* destination: a tracking failure
must never cost an eval sweep the results already in `app.db`, and must never fail a
training run that is otherwise fine. So every method here swallows, and a mirror
that failed to start degrades to a no-op rather than to an exception at each call
site.

`evals.sweep` had this as a private class. `training.metrics` needed the same thing,
and a second copy of "wrapped in swallows throughout" is exactly the kind of
duplication that drifts — one of them gains a guard, the other does not, and the
difference only shows up when something breaks.

Deliberately thin. It does not own metric *names*, step numbering or run naming:
those are the producer's business and mean different things (an eval's step is a
case index, a training run's is an optimizer step).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunMirror:
    """One localtrack run, mirrored from somewhere else.

    Construct it and check nothing: `active` is False when localtrack could not be
    reached, and every method is a no-op in that state.
    """

    def __init__(
        self,
        project: str,
        *,
        name: str,
        config: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.run_id = ""
        #: Set when the project was named but could not be opened, so a caller that
        #: wants to warn once can tell "off" from "broken". Nothing here raises.
        self.error = ""
        if not project:
            return
        try:
            from backend.modules.localtrack import store as lt

            lt.create_project(project, project)
            created = lt.create_run(
                # Positional and first. Passing None lets localtrack mint an id
                # rather than colliding with the producer's own id space.
                run_id,
                project_id=project,
                name=name,
                config=config or {},
                tags=tags or [],
            )
            self.run_id = created.id
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            logger.debug("localtrack: could not open a mirror run", exc_info=True)

    @property
    def active(self) -> bool:
        return bool(self.run_id)

    def log(
        self, step: int, metrics: dict[str, float], epoch: float | None = None
    ) -> None:
        """One step's metrics.

        A single `MetricLogItem` carrying every key rather than one per metric: the
        model holds a `metrics` dict, so a step is one row on the wire and one
        coalesced broadcast rather than N of each.
        """
        if not self.run_id or not metrics:
            return
        try:
            from backend.modules.localtrack import store as lt
            from backend.modules.localtrack.models import MetricLogItem

            lt.ingest_metrics(
                [
                    MetricLogItem(
                        run_id=self.run_id,
                        step=step,
                        epoch=epoch,
                        metrics={k: float(v) for k, v in metrics.items()},
                    )
                ]
            )
        except Exception:  # noqa: BLE001
            logger.debug("localtrack: metric ingest failed", exc_info=True)

    def finish(
        self, status: str = "finished", summary: dict[str, Any] | None = None
    ) -> None:
        if not self.run_id:
            return
        try:
            from backend.modules.localtrack import store as lt

            lt.update_run(self.run_id, status=status, summary=summary or {})
        except Exception:  # noqa: BLE001
            logger.debug("localtrack: finish failed", exc_info=True)
