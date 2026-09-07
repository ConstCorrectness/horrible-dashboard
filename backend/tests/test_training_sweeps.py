"""Sweeps: expansion, refusal, execution, and the terminal status they depend on.

A sweep is the first thing in this module that needs a run to *end*. Everything
before it watched one run at a time, so "still running" was indistinguishable from
"finished and nothing else started". Twelve points make that visible immediately,
which is why the terminal-status tests live here.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from backend.modules.training import metrics, recipes, sweeps
from backend.modules.training.models import ProjectModel
from backend.modules.training.runners.script_runner import script_runner


@pytest.fixture(autouse=True)
def _clean():
    sweeps.reset()
    metrics.reset()
    yield
    sweeps.reset()
    metrics.reset()


def _recipe(**kw) -> recipes.Recipe:
    return recipes.Recipe(base_model="m", dataset="d/s", **kw)


def _spec(**kw) -> sweeps.SweepSpec:
    return sweeps.SweepSpec(**kw)


# --- expansion ---------------------------------------------------------------


def test_a_grid_is_every_combination_and_each_point_gets_its_own_output_dir():
    """Sharing an output dir means twelve runs overwrite one checkpoint and the
    sweep produces exactly one model."""
    points = sweeps.expand(
        _recipe(),
        _spec(
            axes=[sweeps.Axis("learning_rate", [1e-4, 2e-4]), sweeps.Axis("r", [8, 16])]
        ),
    )
    assert len(points) == 4
    assert len({p.recipe.output_dir for p in points}) == 4
    assert {
        (p.recipe.values["learning_rate"], p.recipe.values["r"]) for p in points
    } == {
        (1e-4, 8),
        (1e-4, 16),
        (2e-4, 8),
        (2e-4, 16),
    }


def test_a_zip_steps_every_axis_together():
    """For knobs that must move in step — rank and alpha, usually."""
    points = sweeps.expand(
        _recipe(),
        _spec(
            strategy="zip",
            axes=[sweeps.Axis("r", [8, 32]), sweeps.Axis("lora_alpha", [16, 64])],
        ),
    )
    assert [(p.recipe.values["r"], p.recipe.values["lora_alpha"]) for p in points] == [
        (8, 16),
        (32, 64),
    ]


def test_a_zip_with_ragged_axes_is_refused_rather_than_truncated():
    problems = sweeps.validate(
        _recipe(),
        _spec(
            strategy="zip",
            axes=[sweeps.Axis("r", [1, 2]), sweeps.Axis("seed", [1, 2, 3])],
        ),
    )
    assert any("same number of values" in p for p in problems)


def test_a_random_sweep_is_seeded_so_it_can_be_repeated():
    spec = _spec(
        strategy="random",
        count=3,
        seed=7,
        axes=[sweeps.Axis("r", [8, 16, 32, 64]), sweeps.Axis("seed", [1, 2, 3])],
    )
    first = [p.overrides for p in sweeps.expand(_recipe(), spec)]
    second = [p.overrides for p in sweeps.expand(_recipe(), spec)]
    assert len(first) == 3
    assert first == second


def test_an_axis_naming_a_field_the_task_lacks_is_refused():
    """Otherwise every point is identical and the comparison shows no effect,
    which reads as 'this knob does nothing'."""
    problems = sweeps.validate(_recipe(), _spec(axes=[sweeps.Axis("beta", [0.1, 0.2])]))
    assert any("not a field of trl/sft" in p for p in problems)
    # ...and the same axis IS valid on the task that has it.
    assert not sweeps.validate(
        _recipe(task="dpo"), _spec(axes=[sweeps.Axis("beta", [0.1, 0.2])])
    )


def test_a_single_valued_axis_is_refused_because_it_measures_nothing():
    problems = sweeps.validate(_recipe(), _spec(axes=[sweeps.Axis("seed", [42])]))
    assert any("does not vary" in p for p in problems)


def test_an_oversized_grid_is_refused_with_the_count():
    with pytest.raises(sweeps.SweepError, match="over the limit"):
        sweeps.expand(
            _recipe(),
            _spec(
                axes=[
                    sweeps.Axis("r", list(range(9))),
                    sweeps.Axis("seed", list(range(9))),
                ]
            ),
        )


def test_expansion_does_not_mutate_the_base_recipe():
    base = _recipe()
    before = dict(base.values)
    sweeps.expand(base, _spec(axes=[sweeps.Axis("r", [8, 16])]))
    assert base.values == before
    assert base.output_dir == "outputs/run1"


def test_the_spec_round_trips_through_its_dict():
    spec = _spec(
        axes=[sweeps.Axis("r", [8, 16])],
        strategy="random",
        count=2,
        seed=9,
        max_parallel=3,
    )
    again = sweeps.SweepSpec.from_dict(spec.to_dict())
    assert again.to_dict() == spec.to_dict()


# --- execution ---------------------------------------------------------------


@pytest.fixture
def project(tmp_path) -> ProjectModel:
    """A project whose 'venv python' is this interpreter, so scripts really run."""
    root = tmp_path / "proj"
    root.mkdir()
    venv = root / (".venv/Scripts" if sys.platform == "win32" else ".venv/bin")
    venv.mkdir(parents=True)
    target = venv / ("python.exe" if sys.platform == "win32" else "python")
    try:
        target.symlink_to(sys.executable)
    except (OSError, NotImplementedError):
        import shutil

        shutil.copy2(sys.executable, target)
    return ProjectModel(
        id="p", name="p", root=str(root), python="3.12", venv_ready=True
    )


def _wait(sweep_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = sweeps.status(sweep_id)
        if found and found[0]["state"] in ("finished", "stopped"):
            return found[0]
        time.sleep(0.05)
    raise AssertionError(f"sweep {sweep_id} never finished: {sweeps.status(sweep_id)}")


def test_each_point_writes_its_own_script_carrying_its_own_values(project, monkeypatch):
    """The scripts are a flattening of the same `materialize` the notebook uses —
    a sweep and a manual run must not disagree about what a recipe means."""
    started: list[str] = []
    monkeypatch.setattr(
        script_runner,
        "start",
        lambda proj, script: started.append(script) or _FakeRun(),
    )
    sweep_id = sweeps.start(
        project,
        _recipe(),
        _spec(axes=[sweeps.Axis("learning_rate", [1e-4, 3e-4])]),
        recipes.Introspection(error="no venv"),
    )
    _wait(sweep_id)
    assert len(started) == 2
    sources = [
        (Path(project.root) / s).read_text(encoding="utf-8") for s in sorted(started)
    ]
    assert "learning_rate=0.0001" in sources[0]
    assert "learning_rate=0.0003" in sources[1]
    # Each names its own run, so twelve rows are told apart by what varied.
    assert "ht.callback(name='learning_rate=0.0001')" in sources[0]


def test_a_point_declares_its_config_before_launching(project, monkeypatch):
    """The whole mechanism behind 'which knob caused this': localtrack's
    config_json has existed forever with no producer that filled it usefully."""
    monkeypatch.setattr(script_runner, "start", lambda proj, script: _FakeRun())
    sweep_id = sweeps.start(
        project,
        _recipe(),
        _spec(axes=[sweeps.Axis("r", [8, 16])]),
        recipes.Introspection(error="no venv"),
    )
    _wait(sweep_id)
    declared = metrics._declared
    assert ("p", "r=8") in declared
    assert declared[("p", "r=8")]["r"] == 8
    assert declared[("p", "r=8")]["_axes"] == ["r"]
    assert declared[("p", "r=16")]["r"] == 16


def test_a_sweep_really_runs_its_points_and_records_terminal_status(project):
    """End to end through the script runner with a real interpreter."""
    script_dir = Path(project.root) / sweeps.SCRIPT_DIR
    sweep_id = sweeps.start(
        project,
        _recipe(),
        _spec(axes=[sweeps.Axis("seed", [1, 2])]),
        recipes.Introspection(error="no venv"),
    )
    # The generated scripts import trl, which is not installed here — so both
    # points fail, which is exactly the case that must be *reported* rather than
    # left hanging as "running".
    record = _wait(sweep_id)
    assert record["state"] == "finished"
    assert record["total"] == 2
    assert record["done"] + record["failed"] == 2
    assert script_dir.is_dir()
    for entry in record["results"]:
        assert entry["state"] in ("finished", "failed")


def test_a_failing_point_does_not_stop_the_others(project, monkeypatch):
    calls = {"n": 0}

    def start(proj, script):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("no such script")
        return _FakeRun()

    monkeypatch.setattr(script_runner, "start", start)
    sweep_id = sweeps.start(
        project,
        _recipe(),
        _spec(axes=[sweeps.Axis("seed", [1, 2, 3])]),
        recipes.Introspection(error="no venv"),
    )
    record = _wait(sweep_id)
    assert record["failed"] == 1
    assert record["done"] == 2


def test_stopping_a_sweep_marks_it_stopped_and_keeps_finished_points(
    project, monkeypatch
):
    monkeypatch.setattr(
        script_runner, "start", lambda proj, script: _FakeRun(delay=0.4)
    )
    monkeypatch.setattr(script_runner, "stop", lambda run_id: True)
    sweep_id = sweeps.start(
        project,
        _recipe(),
        _spec(axes=[sweeps.Axis("seed", [1, 2, 3, 4])]),
        recipes.Introspection(error="no venv"),
    )
    time.sleep(0.1)
    assert sweeps.stop(sweep_id) is True
    record = _wait(sweep_id)
    assert record["state"] == "stopped"
    # Not every point ran, and that is the point of stopping.
    assert len(record["results"]) < 4


def test_stopping_an_unknown_sweep_is_false_not_an_error():
    assert sweeps.stop("nope") is False


def test_the_spec_persists_beside_the_recipe(project):
    spec = _spec(axes=[sweeps.Axis("r", [8, 16])], note="rank ablation")
    sweeps.save_spec(project, spec)
    assert json.loads((Path(project.root) / "sweep.json").read_text())["note"] == (
        "rank ablation"
    )
    assert sweeps.load_spec(project).to_dict() == spec.to_dict()


def test_a_missing_or_corrupt_spec_reads_as_empty_rather_than_raising(project):
    assert sweeps.load_spec(project).axes == []
    (Path(project.root) / "sweep.json").write_text("{ not json", encoding="utf-8")
    assert sweeps.load_spec(project).axes == []


class _FakeRun:
    """A ScriptRun stand-in: starts running, finishes shortly after."""

    def __init__(self, delay: float = 0.05, returncode: int = 0) -> None:
        self.id = f"run-{id(self):x}"
        self.returncode: int | None = None
        self.metric_runs: list[str] = []
        self._until = time.monotonic() + delay
        self._code = returncode

    @property
    def running(self) -> bool:
        if time.monotonic() < self._until:
            return True
        self.returncode = self._code
        return False
