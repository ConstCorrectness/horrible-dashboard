"""Push agent trajectories into a horrible-dashboard node.

    from horrible_trajectories import TrajectoryRecorder

    rec = TrajectoryRecorder(dataset="my-coding-agent")
    with rec.run(goal="fix the failing test") as run:
        run.action("bash", {"cmd": "pytest"}, {"rc": 0}, ok=True, ms=1200)
        run.label("outcome", "success")

Depends on `httpx` and the standard library only. See the project README.
"""

from horrible_trajectories.client import DEFAULT_BASE_URL, Run, TrajectoryRecorder

__all__ = ["DEFAULT_BASE_URL", "Run", "TrajectoryRecorder", "__version__"]

__version__ = "0.1.0"
