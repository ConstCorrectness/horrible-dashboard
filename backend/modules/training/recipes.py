"""The typed recipe surface: a fine-tuning config as a schema, not a blank cell.

A fine-tune is a couple of dozen numbers — learning rate, batch size, LoRA rank,
sequence length — and until now the only place to put them was a notebook cell
you wrote from memory. This module makes them a **schema**: a curated catalog of
fields (`FIELDS`), each with a type, a default and a one-line description, which
the pane renders as a form and which **materializes into the existing notebook**.

Three rules keep it from becoming a lie:

- **The schema is validated against what is actually installed.** `trl` renamed
  `SFTConfig.max_seq_length` to `max_length`, `evaluation_strategy` became
  `eval_strategy` in `transformers`, and a recipe that emits the wrong one fails
  with a `TypeError` several minutes into a run. So the field catalog carries
  `aliases`, and `introspect()` asks the project venv which names its installed
  classes actually accept. A field the installed library doesn't know is
  **dropped from the emitted code and shown as dropped**, never emitted hopefully.
- **The form is not the whole surface, and says so.** `TrainingArguments` has
  well over a hundred fields; this catalog has a few dozen. `Introspection.extra`
  counts the difference so the pane can say "24 of 118 knobs — edit the cell for
  the rest" rather than implying the form is the API.
- **There is no new execution path.** `materialize()` returns notebook cells.
  They run on the same kernel, in the same venv, emitting the same
  `@@HORRIBLE@@` sentinel lines as hand-written code, and the same notebook is
  what Kaggle/Colab push already sends. The recipe is stored in `project.json`
  so the *form* round-trips; the cells are yours the moment they land, and are
  never read back.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.modules.training import notebooks
from backend.modules.training.backends.base import (
    RecipeField,
    all_backends,
    get_backend,
)
from backend.modules.training.envs import python_path, venv_exists
from backend.modules.training.models import ProjectModel

#: Re-exported: `RecipeField` moved to the backends package so a backend can
#: declare its catalog without importing the module that consumes it. Callers that
#: imported it from here keep working.
__all__ = ["Recipe", "RecipeField", "introspect", "materialize", "materialize_script"]

logger = logging.getLogger(__name__)


#: The tasks the installed backends can train, in backend order. Was a hardcoded
#: `("sft",)`; it is now whatever `backends/` declares, so adding DPO is adding a
#: TaskSpec rather than editing this module.
def tasks() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for backend in all_backends().values():
        for spec in backend.tasks():
            entry = spec.to_dict()
            entry["backend"] = backend.id
            out.append(entry)
    return out


def backends() -> list[dict[str, Any]]:
    return [
        {
            "id": b.id,
            "label": b.label,
            "blurb": b.blurb,
            "tasks": [t.id for t in b.tasks()],
        }
        for b in all_backends().values()
    ]


TRACKERS = ("none", "tensorboard", "wandb", "mlflow", "trackio")


def catalog(
    backend_id: str = "trl", task: str = "sft", use_lora: bool = True
) -> list[RecipeField]:
    """Every knob this backend renders for this task, in form order."""
    try:
        return list(get_backend(backend_id).fields(task, use_lora))
    except ValueError:
        return list(get_backend("trl").fields("sft", use_lora))


def fields_for(
    target: str, backend_id: str = "trl", task: str = "sft", use_lora: bool = True
) -> list[RecipeField]:
    """The knobs belonging to one emitted object (`config`, `lora`, ...)."""
    return [f for f in catalog(backend_id, task, use_lora) if f.target == target]


def defaults(backend_id: str = "trl", task: str = "sft") -> dict[str, Any]:
    """Every knob's default, LoRA included.

    LoRA fields are always present in the defaults even when `use_lora` is off:
    the values dict is the *form's* memory, and dropping them on a toggle would
    lose a rank the user set before turning LoRA off and back on.
    """
    return {f.name: f.default for f in catalog(backend_id, task, True)}


# --- what the project venv actually has -------------------------------------

#: The script asks the *installed* classes what they accept. Run in the project
#: venv, because that is where trl/peft/torch live — the backend env has none of
#: them and never will.
#:
#: The class list is **supplied by the backend** (`probe_classes(task)`) rather
#: than hardcoded, which is what lets a new task or a whole new framework inherit
#: the `ok | renamed | unsupported | unvalidated` resolution below without writing
#: a second validation path. `__TARGETS__` is replaced with a JSON
#: `{target: "module:Class"}` map before the script runs.
_PROBE = r"""
import json

def accepted(module, name):
    try:
        mod = __import__(module, fromlist=[name])
        cls = getattr(mod, name)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        import dataclasses
        if dataclasses.is_dataclass(cls):
            return sorted(f.name for f in dataclasses.fields(cls)), ""
    except Exception:
        pass
    try:
        import inspect
        params = inspect.signature(cls).parameters
        return sorted(p for p in params if p not in ("self", "args", "kwargs")), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

def version(dist):
    try:
        from importlib.metadata import version as v
        return v(dist)
    except Exception:
        return ""

targets = json.loads(__TARGETS__)
found = {}
errors = {}
for target, ref in targets.items():
    module, _, name = ref.partition(":")
    names, err = accepted(module, name)
    found[target] = names
    errors[target] = err

print(json.dumps({
    "accepted": found,
    "errors": errors,
    "versions": {
        d: version(d) for d in (
            "trl", "transformers", "peft", "torch", "datasets", "accelerate",
            "unsloth", "bitsandbytes", "torchtitan", "nanotron",
        )
    },
}))
"""


@dataclass
class Introspection:
    """What the project venv says about the classes this recipe emits."""

    available: bool = False
    versions: dict[str, str] = field(default_factory=dict)
    accepted: dict[str, list[str]] = field(default_factory=dict)
    #: Fields the installed class accepts that this catalog does not render.
    extra: dict[str, int] = field(default_factory=dict)
    #: target -> the distribution the class came from, so a "renamed" note can
    #: name the library that renamed it without guessing from the target's name.
    libraries: dict[str, str] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "versions": self.versions,
            "accepted": self.accepted,
            "extra": self.extra,
            "libraries": self.libraries,
            "error": self.error,
        }


_cache: dict[str, tuple[float, Introspection]] = {}
_cache_lock = threading.Lock()


def introspect(
    project: ProjectModel,
    *,
    refresh: bool = False,
    backend_id: str = "trl",
    task: str = "sft",
) -> Introspection:
    """Ask the project venv which config fields it accepts.

    One subprocess, cached on the venv's mtime **and the backend/task**, since
    DPOConfig and SFTConfig accept different names and one cache entry for both
    would report the wrong set for whichever was asked second. A fresh probe per
    keystroke in the form would spawn a Python per keystroke, and the answer only
    changes when something is installed.
    """
    if not venv_exists(project):
        return Introspection(
            error="this project has no venv yet, so nothing can be validated"
        )
    try:
        backend = get_backend(backend_id)
    except ValueError as exc:
        return Introspection(error=str(exc))
    key = f"{project.id}:{backend.id}:{task}"
    marker = _venv_mtime(project)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == marker and not refresh:
            return cached[1]

    result = _probe(project, backend, task)
    with _cache_lock:
        _cache[key] = (marker, result)
    return result


def _venv_mtime(project: ProjectModel) -> float:
    try:
        return python_path(project).parent.stat().st_mtime
    except OSError:
        return 0.0


def _probe(project: ProjectModel, backend: Any, task: str) -> Introspection:
    targets = backend.probe_classes(task)
    script = _PROBE.replace("__TARGETS__", repr(json.dumps(targets)))
    try:
        # Blocking `Popen`/`run` on the caller's thread, never asyncio: under
        # `uvicorn --reload` on Windows the loop cannot spawn subprocesses.
        out = subprocess.run(
            [str(python_path(project)), "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project.root,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Introspection(error=f"could not run the project's python: {exc}")
    if out.returncode != 0:
        return Introspection(error=(out.stderr or "the probe failed").strip()[:400])
    try:
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Introspection(error="the probe printed nothing readable")

    accepted = {k: v for k, v in (data.get("accepted") or {}).items() if v}
    errors = data.get("errors") or {}
    if not accepted:
        detail = "; ".join(str(v) for v in errors.values() if v)
        libraries = sorted({ref.partition(":")[0] for ref in targets.values()})
        return Introspection(
            versions=data.get("versions") or {},
            error=(
                f"{' and '.join(libraries)} {'is' if len(libraries) == 1 else 'are'} "
                "not installed in this project's venv, so the recipe can't be "
                f"validated ({detail}). Install the stack from the recipe pane."
            ),
        )
    ours = {
        t: {f.name for f in catalog(backend.id, task, True) if f.target == t}
        for t in targets
    }
    extra = {
        target: max(0, len(names) - len(ours.get(target, set()) & set(names)))
        for target, names in accepted.items()
    }
    return Introspection(
        available=True,
        versions={k: v for k, v in (data.get("versions") or {}).items() if v},
        accepted=accepted,
        extra=extra,
        libraries={t: ref.partition(":")[0] for t, ref in targets.items()},
        error="; ".join(str(v) for v in errors.values() if v),
    )


# --- resolving a field against that ------------------------------------------


@dataclass
class Resolved:
    """How one field will be emitted, and why."""

    field: RecipeField
    #: The kwarg actually emitted — the field's name, one of its aliases, or None
    #: when the installed library accepts neither and it must be dropped.
    emit: str | None
    status: str  # ok | renamed | unsupported | unvalidated
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.field.name,
            "emit": self.emit,
            "status": self.status,
            "note": self.note,
        }


def resolve(recipe_field: RecipeField, intro: Introspection) -> Resolved:
    """Which name to emit for a field, against the installed library.

    Four states, and the difference between the last two is the whole point:
    `unvalidated` means we never got to ask (no venv, no trl), so the field is
    emitted hopefully and labelled as such; `unsupported` means we asked and the
    answer was no, so it is **not** emitted at all.
    """
    if not intro.available:
        return Resolved(recipe_field, recipe_field.name, "unvalidated")
    accepted = set(intro.accepted.get(recipe_field.target) or [])
    if recipe_field.name in accepted:
        return Resolved(recipe_field, recipe_field.name, "ok")
    for alias in recipe_field.aliases:
        if alias in accepted:
            # Which library renamed it. Read from the probe's own target map
            # rather than guessed from the target name: `sft` used to imply trl,
            # but a target is now a backend's own word and `config` means trl for
            # one backend and unsloth for another.
            library = intro.libraries.get(recipe_field.target, "the library")
            version = intro.versions.get(library, "")
            return Resolved(
                recipe_field,
                alias,
                "renamed",
                f"{library} {version} calls this `{alias}`".strip(),
            )
    return Resolved(
        recipe_field,
        None,
        "unsupported",
        "the installed version accepts neither this name nor its known aliases, "
        "so it is left out rather than emitted and rejected mid-run",
    )


def resolve_all(
    intro: Introspection,
    backend_id: str = "trl",
    task: str = "sft",
    use_lora: bool = True,
) -> list[Resolved]:
    return [resolve(f, intro) for f in catalog(backend_id, task, use_lora)]


def warnings_for(values: dict[str, Any], trackers: list[str]) -> list[str]:
    """Configuration mistakes worth saying out loud before a multi-hour run."""
    out: list[str] = []
    if values.get("bf16") and values.get("fp16"):
        out.append(
            "bf16 and fp16 are both on — they are alternatives, and enabling both "
            "is a configuration error rather than a stronger version of one."
        )
    if "wandb" in trackers:
        from backend.modules.training import trackers as tracker_creds

        if not tracker_creds.has_wandb_key():
            out.append(
                "Weights & Biases is selected but no API key is connected, so the "
                "run will stop at a login prompt. Connect the Experiment trackers "
                "tile first."
            )
    if "mlflow" in trackers:
        from backend.modules.training import trackers as tracker_creds

        if not tracker_creds.mlflow_uri():
            out.append(
                "MLflow is selected but no tracking URI is configured, so runs will "
                "be written to a local ./mlruns directory."
            )
    return out


# --- materialization ----------------------------------------------------------


@dataclass
class Recipe:
    #: Which framework emits the code. Defaults to trl, so every recipe written
    #: before backends existed keeps meaning exactly what it meant.
    backend: str = "trl"
    task: str = "sft"
    base_model: str = ""
    dataset: str = ""
    dataset_split: str = "train"
    #: A registered dataset (`backend.modules.datasets.registry`). When set it
    #: **wins** over the free-text `dataset` above, and carries the detected format
    #: and column map with it — which is the difference between "this run used
    #: Capybara" and a rerun that provably eats the same rows in the same shape.
    #: The free-text field stays supported: typing a Hub id has to keep working.
    dataset_id: str = ""
    #: Role -> column, from the registered dataset or a hand correction. Empty
    #: means "detect at materialize time".
    column_map: dict[str, str] = field(default_factory=dict)
    #: The column holding the text to train on. `messages` is the chat format trl
    #: applies the model's own template to. Derived from the format when a
    #: registered dataset is used, rather than typed from memory.
    text_field: str = "text"
    use_lora: bool = True
    output_dir: str = "outputs/run1"
    trackers: list[str] = field(default_factory=lambda: ["none"])
    values: dict[str, Any] = field(default_factory=defaults)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "task": self.task,
            "baseModel": self.base_model,
            "dataset": self.dataset,
            "datasetId": self.dataset_id,
            "datasetSplit": self.dataset_split,
            "columnMap": dict(self.column_map),
            "textField": self.text_field,
            "useLora": self.use_lora,
            "outputDir": self.output_dir,
            "trackers": list(self.trackers),
            "values": dict(self.values),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Recipe:
        backend_id = str(data.get("backend") or "trl")
        task = str(data.get("task") or "sft")
        merged = defaults(backend_id, task)
        merged.update(data.get("values") or {})
        return Recipe(
            backend=backend_id,
            task=task,
            base_model=str(data.get("baseModel") or ""),
            dataset=str(data.get("dataset") or ""),
            dataset_id=str(data.get("datasetId") or ""),
            column_map={
                str(k): str(v) for k, v in (data.get("columnMap") or {}).items()
            },
            dataset_split=str(data.get("datasetSplit") or "train"),
            text_field=str(data.get("textField") or "text"),
            use_lora=bool(data.get("useLora", True)),
            output_dir=str(data.get("outputDir") or "outputs/run1"),
            trackers=[str(t) for t in (data.get("trackers") or ["none"])],
            values=merged,
        )


def report_to(recipe: Recipe) -> list[str]:
    """`report_to` as the library wants it: a list, `["none"]` for nothing."""
    picked = [t for t in recipe.trackers if t in TRACKERS and t != "none"]
    return picked or ["none"]


def _literal(value: Any) -> str:
    return repr(value)


def kwargs_for(recipe: Recipe, target: str, intro: Introspection) -> list[str]:
    """The `name=value,` lines for one emitted object, resolved against the venv.

    Public because every backend needs it and none of them should reimplement the
    dropped/renamed handling — that consistency is the module's whole claim.
    """
    lines: list[str] = []
    for recipe_field in fields_for(
        target, recipe.backend, recipe.task, recipe.use_lora
    ):
        resolved = resolve(recipe_field, intro)
        if resolved.emit is None:
            lines.append(
                f"    # {recipe_field.name}: dropped — {resolved.note.split(',')[0]}"
            )
            continue
        value = recipe.values.get(recipe_field.name, recipe_field.default)
        comment = f"  # {resolved.note}" if resolved.status == "renamed" else ""
        lines.append(f"    {resolved.emit}={_literal(value)},{comment}")
    return lines


@dataclass
class ResolvedDataset:
    """What a recipe's dataset fields actually mean, after the registry has spoken."""

    ref: str
    split: str
    config: str = ""
    fmt: str = ""
    column_map: dict[str, Any] = field(default_factory=dict)
    #: Absolute path when the rows are local. A registered local dataset resolves
    #: to one, which is what lets `evals.export`'s jsonl be picked rather than typed.
    path: str = ""
    #: True when the id could not be found. Materialize emits a loud comment rather
    #: than silently falling back to the free-text field, which would train on a
    #: different dataset than the form shows.
    missing: bool = False


def resolve_dataset(recipe: Recipe) -> ResolvedDataset:
    """The dataset a recipe points at: the registered one if there is one.

    A registered id wins over the free-text field because it carries the shape as
    well as the name. A *dangling* id does not fall back — a recipe that says
    "dataset #ab12" and quietly trains on whatever is in the text box is the one
    outcome worse than an error.
    """
    if not recipe.dataset_id:
        return ResolvedDataset(
            ref=recipe.dataset,
            split=recipe.dataset_split,
            column_map=dict(recipe.column_map),
        )
    from backend.modules.datasets import registry as dataset_registry

    found = dataset_registry.get(recipe.dataset_id)
    if found is None:
        return ResolvedDataset(
            ref=recipe.dataset, split=recipe.dataset_split, missing=True
        )
    return ResolvedDataset(
        ref=found.ref,
        split=found.split or recipe.dataset_split,
        config=found.config,
        fmt=found.format,
        # A hand correction on the recipe beats the registered map: it is the more
        # recent statement of intent, and the registry row may be shared.
        column_map=dict(recipe.column_map) or dict(found.column_map),
        path=found.path,
    )


def dataset_call(resolved: ResolvedDataset) -> str:
    """The `load_dataset(...)` line, which is not one shape but three.

    `load_dataset("tatsu-lab/alpaca", split="train")` loads a Hub dataset by id.
    A **local file** needs an entirely different call —
    `load_dataset("json", data_files=..., split=...)` — where the first argument is
    the *loader name* and the path moves into `data_files`. Passing a path as the
    first argument does not fail cleanly; it goes looking for a Hub repo of that
    name.

    That distinction is what made `evals.export` a dead end. It writes a
    training-ready SFT `.jsonl` into `$HORRIBLE_DATA_DIR/evals/exports/`, the whole
    point of which is to fine-tune on what a model got wrong — and no recipe could
    load it, so the last spoke of the flywheel was "hand-edit the generated cell".

    The third shape is a **registered dataset**, which resolves to whichever of the
    first two it actually is — including an absolute path for a local one, so
    picking last week's export from the browser emits a call that works from any
    working directory. Raw strings still get the extension sniffing, because typing
    a Hub id has to keep working.

    Detected by shape rather than by a flag on the recipe: a dataset field is either
    a Hub id (`owner/name`, never a path) or a path, and asking the user to also
    tick a box saying which is asking them to restate what they just typed.
    """
    # A local registered dataset knows exactly where its rows are; prefer that to
    # the ref, which is relative to a directory the notebook may not be run from.
    ref = resolved.path or resolved.ref
    split = _literal(resolved.split)
    lowered = ref.lower()
    for suffix, loader in (
        ((".jsonl", ".json", ".ndjson"), "json"),
        ((".csv",), "csv"),
        ((".parquet",), "parquet"),
    ):
        if lowered.endswith(suffix):
            return (
                f"dataset = load_dataset({loader!r}, data_files={_literal(ref)}, "
                f"split={split})"
            )
    if resolved.config:
        # The field people miss: `gsm8k` has no default config, and asking for one
        # without it fails in a way that reads as "the dataset is broken".
        return (
            f"dataset = load_dataset({_literal(ref)}, {_literal(resolved.config)}, "
            f"split={split})"
        )
    return f"dataset = load_dataset({_literal(ref)}, split={split})"


def _reshape_cell(resolved: ResolvedDataset, task: str = "sft") -> tuple[str, str]:
    """(formatting source, text field) for a dataset whose shape needs adapting.

    Emitted **explicitly into the notebook**, never applied invisibly: the contract
    of the recipe surface is that the generated cells are the truth and are yours to
    edit. A hidden transform between the dataset you picked and the tokens the model
    saw would be the one part of the pipeline you could not read.
    """
    if not resolved.fmt or resolved.fmt == "unknown":
        return "", ""
    from backend.modules.datasets import formats as dataset_formats

    columns = {str(k): str(v) for k, v in resolved.column_map.items()}
    detection = dataset_formats.Detection(resolved.fmt, 1.0, "as registered", columns)
    adaptation = dataset_formats.adapt(detection, task)
    if not adaptation.ok:
        return "", ""
    source = (
        dataset_formats.formatting_source(resolved.fmt, adaptation.columns)
        if adaptation.needs_formatting
        else ""
    )
    return source, dataset_formats.text_field_for(resolved.fmt, adaptation.columns)


def header_source(
    recipe: Recipe,
    intro: Introspection,
    resolved: ResolvedDataset,
    reshaped: bool = False,
) -> str:
    """The markdown header every backend's generated block opens with.

    Shared rather than per-backend, because the three things it says — which
    versions this was generated against, which fields were dropped, and whether
    anything was validated at all — are properties of the *surface*, not of the
    framework. A backend that wrote its own would be a backend whose header
    quietly stopped mentioning dropped fields.
    """
    versions = ", ".join(f"{k} {v}" for k, v in sorted(intro.versions.items()) if v)
    dropped = [
        r
        for r in resolve_all(intro, recipe.backend, recipe.task, recipe.use_lora)
        if r.emit is None
    ]
    task_label = next(
        (t["label"] for t in tasks() if t["id"] == recipe.task), recipe.task
    )
    header = [
        f"{marker_cell()} — {task_label}",
        "",
        f"Generated from the recipe form against "
        f"**{versions or 'an unvalidated environment'}**.",
        "",
        "These cells are yours now — edit them freely. The form does **not** read them",
        "back; it round-trips through `recipe.json`, so regenerating overwrites whatever",
        "is here.",
    ]
    if dropped:
        header += [
            "",
            "Left out because the installed libraries don't accept them: "
            + ", ".join(f"`{r.field.name}`" for r in dropped)
            + ".",
        ]
    if not intro.available:
        header += [
            "",
            "> Not validated against an installed library — every field below is emitted",
            "> as written and may be rejected at runtime.",
        ]
    if resolved.missing:
        # Loud, because the alternative is training on whatever is in the text box
        # while the form shows a dataset that no longer exists.
        header += [
            "",
            f"> The registered dataset `{recipe.dataset_id}` no longer exists, so the",
            "> free-text dataset field below was used instead. Re-pick it before running.",
        ]
    elif resolved.fmt and resolved.fmt != "unknown":
        mapped = (
            ", mapped from " + ", ".join(f"`{v}`" for v in resolved.column_map.values())
            if resolved.column_map
            else ""
        )
        header += [
            "",
            f"Dataset shape: **{resolved.fmt}**{mapped}"
            + (". A cell below reshapes it into chat turns." if reshaped else "."),
        ]
    return "\n".join(header)


def materialize(
    recipe: Recipe, intro: Introspection, run_name: str | None = None
) -> list[dict[str, str]]:
    """The recipe as notebook cells, emitted by the recipe's backend.

    Cells, not a new runner: they execute on the same kernel in the same venv,
    their `ht.log()` output is indistinguishable from hand-written code's, and
    the same `main.ipynb` is what the Kaggle and Colab push already send.

    `run_name` names the run in the metrics store. A sweep passes one per point so
    twelve rows are told apart by what varied rather than by a hex id.
    """
    try:
        backend = get_backend(recipe.backend)
    except ValueError:
        # A recipe naming a backend this install does not have (a plugin was
        # uninstalled) falls back to trl rather than failing to render — the form
        # still needs to open so the user can change it.
        logger.info("training: unknown backend %r, using trl", recipe.backend)
        backend = get_backend("trl")
    return backend.materialize(recipe, intro, run_name)


def materialize_script(
    recipe: Recipe, intro: Introspection, run_name: str | None = None
) -> str:
    """The same recipe as a standalone `.py`, for the script runner.

    A **flattening of `materialize()`**, deliberately, rather than a second
    emitter. A sweep runs N configs and a kernel is single and serial, so sweep
    points have to be processes — but the moment the script path grows its own
    `SFTConfig(...)` writer, the notebook and the sweep can disagree about what a
    recipe means, and the sweep is exactly where that matters most. So there is one
    `_kwargs`, one `_dataset_call`, one alias resolution, and this function only
    changes the container.

    Markdown cells become comments; the notebook's bare `dataset` display
    expression is dropped (harmless in a script, but it reads as a mistake).
    """
    out = [
        '"""Generated by the recipe form — regenerating this file overwrites it.',
        "",
        "Run it with the project venv's python; `horrible_train` streams metrics back",
        "to the dashboard through the same sentinel protocol a notebook cell uses.",
        '"""',
        "",
    ]
    for cell in materialize(recipe, intro, run_name):
        source = cell["source"]
        if cell["cell_type"] == "markdown":
            out += [f"# {line}".rstrip() for line in source.splitlines()]
        else:
            out += [line for line in source.splitlines() if line.strip() != "dataset"]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def marker_cell() -> str:
    """First line of the generated header — how a regenerate finds its old cells."""
    return "## Fine-tuning recipe"


# --- persistence --------------------------------------------------------------


def recipe_path(project: ProjectModel) -> Path:
    return Path(project.root) / "recipe.json"


def load_recipe(project: ProjectModel) -> Recipe:
    path = recipe_path(project)
    if not path.is_file():
        return Recipe()
    try:
        return Recipe.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.info("training: unreadable recipe for %s (%s)", project.id, exc)
        return Recipe()


def save_recipe(project: ProjectModel, recipe: Recipe) -> None:
    from backend.atomic_write import write_text_atomic

    write_text_atomic(recipe_path(project), json.dumps(recipe.to_dict(), indent=2))


def apply_to_notebook(
    project: ProjectModel, recipe: Recipe, intro: Introspection
) -> int:
    """Write the recipe's cells into the project notebook. Returns how many landed.

    A regenerate **replaces** the previously generated block rather than appending
    a second copy: the block is found by the marker in its header cell and by the
    cells that followed it last time, recorded in `recipe.json`. Appending instead
    would leave two `SFTConfig` definitions in one notebook, of which the second
    silently wins — the sort of thing you only notice after a wasted run.
    """
    import nbformat

    from backend.notebook_core import notebooks as core_notebooks

    path = notebooks.notebook_path(project, "main.ipynb")
    nb = core_notebooks.load(path)
    cells = materialize(recipe, intro)

    start = _generated_start(nb)
    made = [
        nbformat.v4.new_markdown_cell(c["source"])
        if c["cell_type"] == "markdown"
        else nbformat.v4.new_code_cell(c["source"])
        for c in cells
    ]
    for cell in made:
        cell.metadata["horrible_recipe"] = True

    if start is None:
        nb.cells.extend(made)
    else:
        end = start
        while end < len(nb.cells) and nb.cells[end].metadata.get("horrible_recipe"):
            end += 1
        nb.cells[start:end] = made
    core_notebooks.save(path, nb)
    return len(made)


def _generated_start(nb: Any) -> int | None:
    """Index of the first cell of a previously generated block, if there is one."""
    for index, cell in enumerate(nb.cells):
        if cell.metadata.get("horrible_recipe"):
            return index
        # Cells generated before the metadata flag existed, and cells a user
        # copied by hand: the marker line is the fallback identity.
        if cell.cell_type == "markdown" and str(cell.source).startswith(marker_cell()):
            return index
    return None


async def doc_links() -> list[dict[str, Any]]:
    """Where to read about these fields, for the version actually installed.

    Answered from this node's own crawled doc index (the `trl`/`peft` seeds), so
    the link points at a page we have and whose version we know, and carries the
    index's own version annotation — including the mismatch flag when the indexed
    docs are a different series from what the venv has. An empty index yields an
    empty list rather than a guessed URL.
    """
    from backend.modules.search.providers.crawl import search_index

    out: list[dict[str, Any]] = []
    for query, label in (
        ("SFTConfig SFTTrainer training arguments", "SFTConfig (trl)"),
        ("LoraConfig parameters rank alpha target modules", "LoraConfig (peft)"),
    ):
        try:
            hits = await search_index(query, limit=1)
        except Exception as exc:  # noqa: BLE001 — no index is a normal state
            logger.info("training: no doc index for %r (%s)", label, exc)
            continue
        if not hits:
            continue
        hit = hits[0]
        raw = hit.raw or {}
        out.append(
            {
                "label": label,
                "url": hit.url,
                "title": hit.title,
                "version": raw.get("version"),
                "installedMismatch": raw.get("installed_mismatch"),
            }
        )
    return out
