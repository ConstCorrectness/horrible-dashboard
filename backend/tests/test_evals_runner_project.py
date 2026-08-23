"""Runner B: the harness script, and what it does with what the script prints.

The subprocess itself is faked. Running a real `datasets` install in a unit test
would take minutes and need the network, and the thing worth pinning is not that
`uv` works — it is the contract between this module and the script it writes: what
goes into the job, what comes back, and what happens when the script dies.

The template is compiled for real, though. A generated script that does not parse
is the failure mode of every code-generating module, and it is cheap to rule out.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules.evals import harness, runner_project
from backend.modules.evals.models import EvalCase, Expect, HfBenchmark


def bench_case(case_id: str = "gsm8k", **kwargs) -> EvalCase:
    return EvalCase(
        id=case_id,
        type="hf_benchmark",
        prompt="",
        expect=Expect(grade="subset"),
        benchmark=HfBenchmark(dataset="gsm8k", config="main", **kwargs),
    )


@pytest.fixture
def project(tmp_path):
    return SimpleNamespace(id="p", name="p", root=str(tmp_path), venv_ready=True)


# --- the generated script ----------------------------------------------------


def test_the_generated_script_parses():
    """Every code-generating module's failure mode, ruled out for the price of a
    `compile()`."""
    job = runner_project._job_for(bench_case(), "http://localhost:1234", "m")
    compile(harness.render(job), "run_benchmark.py", "exec")


def test_the_job_survives_quotes_and_braces_in_a_template():
    """A dataset id or template with a quote in it would end the string literal and
    produce a script that does not parse — which is why the job is embedded as
    JSON rather than interpolated field by field."""
    case = bench_case(input_template="Q: {question}\n\"quoted\" 'single' {{literal}}")
    job = runner_project._job_for(case, "http://x", "m")
    source = harness.render(job)
    compile(source, "run_benchmark.py", "exec")

    scope: dict = {}
    exec(  # noqa: S102 - reading back the JOB literal is the point of the test
        source.split("def log(")[0].replace("import urllib.request", ""), scope
    )
    assert scope["JOB"]["input_template"] == case.benchmark.input_template


def test_the_job_carries_everything_the_script_needs():
    job = runner_project._job_for(
        bench_case(split="test[:7]", metric="contains", system="Be terse."),
        "http://localhost:9",
        "my-model",
    )
    assert job["dataset"] == "gsm8k"
    assert job["config"] == "main"
    assert job["split"] == "test[:7]"
    assert job["metric"] == "contains"
    assert job["system"] == "Be terse."
    assert job["endpoint"] == "http://localhost:9"
    assert job["model"] == "my-model"
    assert job["timeout"] == runner_project.ROW_TIMEOUT_S


# --- requirements ------------------------------------------------------------


def test_only_the_metrics_that_need_evaluate_pull_it_in():
    """A suite that only ever uses `exact_match` should never wait for `evaluate`
    to resolve."""
    assert runner_project.requirements_for([bench_case()]) == ["datasets"]
    assert runner_project.requirements_for([bench_case(metric="contains")]) == [
        "datasets"
    ]
    assert "evaluate" in runner_project.requirements_for([bench_case(metric="rouge")])


# --- the script's filename ---------------------------------------------------


def test_a_case_id_cannot_escape_the_project_directory(project):
    """A case id is user-authored text and it reaches the filesystem here."""
    path = runner_project.script_path(project, bench_case("../../evil"))
    assert path.parent == Path(project.root) / "evals"
    assert ".." not in path.name


def test_each_case_gets_its_own_script(project):
    """Five benchmarks should leave five readable scripts, not one overwritten
    four times."""
    a = runner_project.script_path(project, bench_case("alpha"))
    b = runner_project.script_path(project, bench_case("beta"))
    assert a != b


# --- reading the result back -------------------------------------------------


@pytest.mark.anyio
async def test_a_score_above_the_threshold_passes(project, monkeypatch):
    monkeypatch.setattr(
        runner_project,
        "_run_script",
        lambda *a, **kw: {
            "score": 0.82,
            "rows": 50,
            "errors": 0,
            "metric": "exact_match",
        },
    )
    result = await runner_project.run_case(
        bench_case(threshold=0.5), project, endpoint="http://x", model="m"
    )
    assert result.passed
    assert "0.820" in result.detail and "50 rows" in result.detail


@pytest.mark.anyio
async def test_a_score_below_the_threshold_fails(project, monkeypatch):
    monkeypatch.setattr(
        runner_project,
        "_run_script",
        lambda *a, **kw: {
            "score": 0.2,
            "rows": 50,
            "errors": 0,
            "metric": "exact_match",
        },
    )
    result = await runner_project.run_case(
        bench_case(threshold=0.5), project, endpoint="http://x", model="m"
    )
    assert not result.passed


@pytest.mark.anyio
async def test_errored_rows_are_surfaced_not_folded_into_the_score(
    project, monkeypatch
):
    """ "Scored 0.4" and "scored 0.4 because a fifth of the rows raised" are
    different results."""
    monkeypatch.setattr(
        runner_project,
        "_run_script",
        lambda *a, **kw: {
            "score": 0.4,
            "rows": 50,
            "errors": 10,
            "metric": "exact_match",
        },
    )
    result = await runner_project.run_case(
        bench_case(), project, endpoint="http://x", model="m"
    )
    assert "10 row(s) errored" in result.detail


@pytest.mark.anyio
async def test_a_harness_error_becomes_a_failed_case_with_the_reason(
    project, monkeypatch
):
    monkeypatch.setattr(
        runner_project,
        "_run_script",
        lambda *a, **kw: {"error": "could not reach the model at http://x"},
    )
    result = await runner_project.run_case(
        bench_case(), project, endpoint="http://x", model="m"
    )
    assert not result.passed
    assert "could not reach the model" in result.error


@pytest.mark.anyio
async def test_the_script_is_written_where_it_can_be_read(project, monkeypatch):
    """It lands in the project as an ordinary file you can open and edit — that is
    half the reason for running benchmarks in the dashboard at all."""
    monkeypatch.setattr(
        runner_project, "_run_script", lambda *a, **kw: {"score": 1.0, "rows": 1}
    )
    case = bench_case("readable")
    await runner_project.run_case(case, project, endpoint="http://x", model="m")

    path = runner_project.script_path(project, case)
    assert path.exists()
    source = path.read_text(encoding="utf-8")
    assert "load_dataset" in source
    assert "gsm8k" in source


@pytest.mark.anyio
async def test_a_case_without_a_benchmark_block_fails_clearly(project):
    case = EvalCase(id="broken", type="hf_benchmark", prompt="")
    result = await runner_project.run_case(
        case, project, endpoint="http://x", model="m"
    )
    assert not result.passed
    assert "no benchmark block" in result.detail


# --- the subprocess contract -------------------------------------------------


def test_the_sentinel_line_is_parsed_and_the_rest_is_progress(tmp_path, monkeypatch):
    """The script prints progress AND one machine-readable line. Parsing a score
    out of prose is how a harness starts reporting numbers nobody can reproduce."""
    script = tmp_path / "fake.py"
    script.write_text(
        "import json\n"
        "print('loading gsm8k', flush=True)\n"
        "print('  10/10  score 0.700', flush=True)\n"
        f"print({harness.RESULT_SENTINEL!r} + json.dumps({{'score': 0.7, 'rows': 10}}), flush=True)\n",
        encoding="utf-8",
    )
    seen: list[str] = []
    import sys

    payload = runner_project._run_script(
        sys.executable, script, str(tmp_path), seen.append
    )

    assert payload == {"score": 0.7, "rows": 10}
    assert "loading gsm8k" in seen
    assert not any(harness.RESULT_SENTINEL in line for line in seen)


def test_a_script_that_dies_reports_its_last_output(tmp_path):
    """A bare exit code is not something anyone can act on; the last thing the
    script said almost always is."""
    script = tmp_path / "dies.py"
    script.write_text(
        "import sys\nprint('resolving datasets', flush=True)\n"
        "print('ModuleNotFoundError: No module named datasets', flush=True)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    import sys

    payload = runner_project._run_script(
        sys.executable, script, str(tmp_path), lambda _l: None
    )
    assert "exited with 1" in payload["error"]
    assert "No module named datasets" in payload["error"]


def test_unparseable_result_json_says_so(tmp_path):
    script = tmp_path / "garbage.py"
    script.write_text(
        f"print({harness.RESULT_SENTINEL!r} + 'not json', flush=True)\n",
        encoding="utf-8",
    )
    import sys

    payload = runner_project._run_script(
        sys.executable, script, str(tmp_path), lambda _l: None
    )
    assert "unparseable result" in payload["error"]


def test_benchmark_projects_are_marked_as_ours(tmp_path, monkeypatch):
    """`ensure_project` stamps `owner`, so the training pane can tell working storage
    from a project you author in.

    It matters because this path goes straight to `create_project`, which makes the
    directory but **not** a notebook — the create *route* scaffolds that from a
    provider. So the project has no `main.ipynb`, and its venv gets only
    `requirements_for(cases)` (no `ipykernel`). Unmarked, the training pane offered it
    an "Open notebook" button whose only possible outcome was an error.
    """
    import json
    import os

    from backend.modules.training import projects

    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.write_text(json.dumps({"training.projectsRoot": str(tmp_path / "projects")}))

    created = runner_project.ensure_project("starter")
    assert created.owner == runner_project.OWNER
    # The thing that makes the mark necessary, asserted rather than assumed.
    assert not (Path(created.root) / projects.DEFAULT_NOTEBOOK).exists()

    # Reused rather than remade, and the mark survives the round trip through disk.
    again = runner_project.ensure_project("starter")
    assert again.id == created.id
    assert projects.get_project(created.id).owner == runner_project.OWNER


def test_an_older_unmarked_benchmark_project_gets_stamped(tmp_path, monkeypatch):
    """Projects made before `owner` existed are back-filled on the next sweep —
    otherwise the dead notebook button stays for everyone who already ran one."""
    import json
    import os

    from backend.modules.training import projects

    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.write_text(json.dumps({"training.projectsRoot": str(tmp_path / "projects2")}))

    legacy = projects.create_project("evals-legacy", [], "3.12")
    assert legacy.owner == ""

    found = runner_project.ensure_project("legacy")
    assert found.id == legacy.id
    assert projects.get_project(legacy.id).owner == runner_project.OWNER
