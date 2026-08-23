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
from backend.modules.training.envs import python_path, venv_exists
from backend.modules.training.models import ProjectModel

logger = logging.getLogger(__name__)

#: Only SFT is implemented. DPO/GRPO/reward modelling are the same shape and are
#: deliberately absent rather than half-present — a task option that emits a
#: config the trainer rejects is worse than no option.
TASKS = ("sft",)

TARGETS = ("sft", "lora")


@dataclass(frozen=True)
class RecipeField:
    """One knob, as it is rendered and as it is emitted."""

    name: str
    target: str  # sft | lora
    label: str
    type: str  # int | float | bool | text | select
    default: Any
    help: str
    group: str
    options: tuple[str, ...] = ()
    #: Older names for the same idea. The library renames these; we don't get to.
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target": self.target,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "help": self.help,
            "group": self.group,
            "options": list(self.options),
            "aliases": list(self.aliases),
        }


FIELDS: tuple[RecipeField, ...] = (
    # --- data ---
    RecipeField(
        "max_length",
        "sft",
        "Max sequence length",
        "int",
        1024,
        "Tokens per example; longer examples are truncated. The single biggest "
        "lever on memory after batch size.",
        "data",
        aliases=("max_seq_length",),
    ),
    RecipeField(
        "packing",
        "sft",
        "Pack examples",
        "bool",
        False,
        "Concatenate short examples up to the sequence length instead of padding. "
        "Much faster on short data; wrong if examples must not bleed together.",
        "data",
    ),
    # --- optimization ---
    RecipeField(
        "num_train_epochs",
        "sft",
        "Epochs",
        "float",
        1.0,
        "Passes over the dataset.",
        "optimization",
    ),
    RecipeField(
        "per_device_train_batch_size",
        "sft",
        "Batch size (per device)",
        "int",
        1,
        "Examples per step per GPU. The first thing to lower when you run out of "
        "memory.",
        "optimization",
    ),
    RecipeField(
        "gradient_accumulation_steps",
        "sft",
        "Gradient accumulation",
        "int",
        8,
        "Steps to accumulate before an optimizer step. Effective batch size is "
        "this × batch size × devices — the way to keep a large effective batch on "
        "a small card.",
        "optimization",
    ),
    RecipeField(
        "learning_rate",
        "sft",
        "Learning rate",
        "float",
        2e-4,
        "2e-4 is a common LoRA starting point; a full fine-tune wants roughly two "
        "orders of magnitude less.",
        "optimization",
    ),
    RecipeField(
        "lr_scheduler_type",
        "sft",
        "LR schedule",
        "select",
        "cosine",
        "How the learning rate decays over training.",
        "optimization",
        options=("linear", "cosine", "cosine_with_restarts", "constant"),
    ),
    # Two fields and deliberately **not** aliases of each other: transformers 5
    # dropped `warmup_ratio` and kept `warmup_steps`, but they carry different
    # units. Aliasing them would send `0.03` — three percent of training — into a
    # field that means "three hundredths of a step", i.e. no warmup at all, with
    # no error anywhere. Whichever one the installed version accepts is the one
    # that renders; the other is shown as dropped.
    RecipeField(
        "warmup_ratio",
        "sft",
        "Warmup ratio",
        "float",
        0.03,
        "Fraction of total steps spent ramping the learning rate up from zero. "
        "Removed in transformers 5 in favour of warmup_steps.",
        "optimization",
    ),
    RecipeField(
        "warmup_steps",
        "sft",
        "Warmup steps",
        "int",
        0,
        "Steps spent ramping the learning rate up from zero. A count, not a "
        "fraction — the two are not interchangeable.",
        "optimization",
    ),
    RecipeField(
        "weight_decay",
        "sft",
        "Weight decay",
        "float",
        0.0,
        "L2 regularization on the optimizer.",
        "optimization",
    ),
    RecipeField(
        "max_grad_norm",
        "sft",
        "Gradient clipping",
        "float",
        1.0,
        "Clip gradients to this norm. The usual guard against a loss spike ending "
        "a run.",
        "optimization",
    ),
    RecipeField(
        "optim",
        "sft",
        "Optimizer",
        "select",
        "adamw_torch",
        "`adamw_8bit` needs bitsandbytes and roughly halves optimizer memory.",
        "optimization",
        options=("adamw_torch", "adamw_8bit", "adafactor", "sgd"),
    ),
    RecipeField(
        "seed",
        "sft",
        "Seed",
        "int",
        42,
        "Fixes shuffling and initialization, so a rerun is a rerun.",
        "optimization",
    ),
    # --- memory ---
    RecipeField(
        "bf16",
        "sft",
        "bfloat16",
        "bool",
        True,
        "Half precision with fp32's exponent range. Needs Ampere or newer; on "
        "older cards use fp16 instead.",
        "memory",
    ),
    RecipeField(
        "fp16",
        "sft",
        "float16",
        "bool",
        False,
        "Half precision for pre-Ampere cards. Enabling both is a configuration "
        "error, not a stronger version of one.",
        "memory",
    ),
    RecipeField(
        "gradient_checkpointing",
        "sft",
        "Gradient checkpointing",
        "bool",
        True,
        "Recompute activations in the backward pass: much less memory, roughly "
        "20-30% slower.",
        "memory",
    ),
    # --- logging / checkpoints ---
    RecipeField(
        "logging_steps",
        "sft",
        "Log every N steps",
        "int",
        10,
        "How often metrics reach the chart pane (and any tracker you enabled).",
        "logging",
    ),
    RecipeField(
        "save_steps",
        "sft",
        "Checkpoint every N steps",
        "int",
        200,
        "Checkpoints are what the GGUF conversion converts.",
        "logging",
    ),
    RecipeField(
        "save_total_limit",
        "sft",
        "Keep N checkpoints",
        "int",
        2,
        "Older checkpoints are deleted. A 7B checkpoint is gigabytes.",
        "logging",
    ),
    # --- LoRA ---
    RecipeField(
        "r",
        "lora",
        "LoRA rank",
        "int",
        16,
        "Rank of the update matrices. Higher = more capacity and more memory; 8-32 "
        "covers most tasks.",
        "lora",
    ),
    RecipeField(
        "lora_alpha",
        "lora",
        "LoRA alpha",
        "int",
        32,
        "Scaling for the LoRA update; commonly 2× the rank.",
        "lora",
    ),
    RecipeField(
        "lora_dropout",
        "lora",
        "LoRA dropout",
        "float",
        0.05,
        "Dropout on the LoRA path.",
        "lora",
    ),
    RecipeField(
        "bias",
        "lora",
        "Train biases",
        "select",
        "none",
        "Which bias terms to train alongside the adapters.",
        "lora",
        options=("none", "all", "lora_only"),
    ),
    RecipeField(
        "target_modules",
        "lora",
        "Target modules",
        "text",
        "all-linear",
        "Which submodules get adapters. `all-linear` lets peft pick them per "
        "architecture, which is right far more often than a hand-written list.",
        "lora",
    ),
)

#: Trackers the recipe can turn on. Local metrics are **always** collected via
#: the callback, so this list is genuinely additive — "none" does not mean "no
#: metrics", it means "no third party".
TRACKERS = ("none", "tensorboard", "wandb", "mlflow", "trackio")


def fields_for(target: str) -> list[RecipeField]:
    return [f for f in FIELDS if f.target == target]


def defaults() -> dict[str, Any]:
    return {f.name: f.default for f in FIELDS}


# --- what the project venv actually has -------------------------------------

#: The script asks the *installed* classes what they accept. Run in the project
#: venv, because that is where trl/peft/torch live — the backend env has none of
#: them and never will.
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

sft, sft_err = accepted("trl", "SFTConfig")
lora, lora_err = accepted("peft", "LoraConfig")
print(json.dumps({
    "accepted": {"sft": sft, "lora": lora},
    "errors": {"sft": sft_err, "lora": lora_err},
    "versions": {
        "trl": version("trl"),
        "transformers": version("transformers"),
        "peft": version("peft"),
        "torch": version("torch"),
        "datasets": version("datasets"),
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
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "versions": self.versions,
            "accepted": self.accepted,
            "extra": self.extra,
            "error": self.error,
        }


_cache: dict[str, tuple[float, Introspection]] = {}
_cache_lock = threading.Lock()


def introspect(project: ProjectModel, *, refresh: bool = False) -> Introspection:
    """Ask the project venv which config fields it accepts.

    One subprocess, cached on the venv's mtime — a fresh probe per keystroke in
    the form would spawn a Python per keystroke, and the answer only changes when
    something is installed.
    """
    if not venv_exists(project):
        return Introspection(
            error="this project has no venv yet, so nothing can be validated"
        )
    marker = _venv_mtime(project)
    with _cache_lock:
        cached = _cache.get(project.id)
        if cached and cached[0] == marker and not refresh:
            return cached[1]

    result = _probe(project)
    with _cache_lock:
        _cache[project.id] = (marker, result)
    return result


def _venv_mtime(project: ProjectModel) -> float:
    try:
        return python_path(project).parent.stat().st_mtime
    except OSError:
        return 0.0


def _probe(project: ProjectModel) -> Introspection:
    try:
        # Blocking `Popen`/`run` on the caller's thread, never asyncio: under
        # `uvicorn --reload` on Windows the loop cannot spawn subprocesses.
        out = subprocess.run(
            [str(python_path(project)), "-c", _PROBE],
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
        return Introspection(
            versions=data.get("versions") or {},
            error=(
                "trl and peft are not installed in this project's venv, so the "
                f"recipe can't be validated ({detail})"
            ),
        )
    ours = {t: {f.name for f in fields_for(t)} for t in TARGETS}
    extra = {
        target: max(0, len(names) - len(ours.get(target, set()) & set(names)))
        for target, names in accepted.items()
    }
    return Introspection(
        available=True,
        versions={k: v for k, v in (data.get("versions") or {}).items() if v},
        accepted=accepted,
        extra=extra,
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
            library = "trl" if recipe_field.target == "sft" else "peft"
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


def resolve_all(intro: Introspection) -> list[Resolved]:
    return [resolve(f, intro) for f in FIELDS]


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
    task: str = "sft"
    base_model: str = ""
    dataset: str = ""
    dataset_split: str = "train"
    #: The column holding the text to train on. `messages` is the chat format trl
    #: applies the model's own template to.
    text_field: str = "text"
    use_lora: bool = True
    output_dir: str = "outputs/run1"
    trackers: list[str] = field(default_factory=lambda: ["none"])
    values: dict[str, Any] = field(default_factory=defaults)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "baseModel": self.base_model,
            "dataset": self.dataset,
            "datasetSplit": self.dataset_split,
            "textField": self.text_field,
            "useLora": self.use_lora,
            "outputDir": self.output_dir,
            "trackers": list(self.trackers),
            "values": dict(self.values),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Recipe:
        merged = defaults()
        merged.update(data.get("values") or {})
        return Recipe(
            task=str(data.get("task") or "sft"),
            base_model=str(data.get("baseModel") or ""),
            dataset=str(data.get("dataset") or ""),
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


def _kwargs(recipe: Recipe, target: str, intro: Introspection) -> list[str]:
    lines: list[str] = []
    for recipe_field in fields_for(target):
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


def _dataset_call(recipe: Recipe) -> str:
    """The `load_dataset(...)` line, which is not one shape but two.

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

    Detected by shape rather than by a flag on the recipe: a dataset field is either
    a Hub id (`owner/name`, never a path) or a path, and asking the user to also
    tick a box saying which is asking them to restate what they just typed.
    """
    ref = recipe.dataset
    split = _literal(recipe.dataset_split)
    lowered = ref.lower()
    if lowered.endswith((".jsonl", ".json")):
        return f"dataset = load_dataset('json', data_files={_literal(ref)}, split={split})"
    if lowered.endswith(".csv"):
        return f"dataset = load_dataset('csv', data_files={_literal(ref)}, split={split})"
    if lowered.endswith(".parquet"):
        return f"dataset = load_dataset('parquet', data_files={_literal(ref)}, split={split})"
    return f"dataset = load_dataset({_literal(ref)}, split={split})"


def materialize(recipe: Recipe, intro: Introspection) -> list[dict[str, str]]:
    """The recipe as notebook cells.

    Cells, not a new runner: they execute on the same kernel in the same venv,
    their `ht.log()` output is indistinguishable from hand-written code's, and
    the same `main.ipynb` is what the Kaggle and Colab push already send.
    """
    versions = ", ".join(f"{k} {v}" for k, v in sorted(intro.versions.items()) if v)
    dropped = [r for r in resolve_all(intro) if r.emit is None]
    header = [
        "## Fine-tuning recipe",
        "",
        f"Generated from the recipe form against **{versions or 'an unvalidated environment'}**.",
        "",
        "These cells are yours now — edit them freely. The form does **not** read them",
        "back; it round-trips through `project.json`, so regenerating overwrites whatever",
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

    cells: list[dict[str, str]] = [
        {"cell_type": "markdown", "source": "\n".join(header)}
    ]

    imports = [
        "import horrible_train as ht",
        "from datasets import load_dataset",
        "from trl import SFTConfig, SFTTrainer",
    ]
    if recipe.use_lora:
        imports.append("from peft import LoraConfig")
    imports += [
        "",
        _dataset_call(recipe),
        "dataset",
    ]
    cells.append({"cell_type": "code", "source": "\n".join(imports)})

    config = ["config = SFTConfig("]
    config.append(f"    output_dir={_literal(recipe.output_dir)},")
    config += _kwargs(recipe, "sft", intro)
    config.append(f"    report_to={_literal(report_to(recipe))},")
    config.append(")")
    cells.append({"cell_type": "code", "source": "\n".join(config)})

    if recipe.use_lora:
        lora = ["peft_config = LoraConfig("]
        lora += _kwargs(recipe, "lora", intro)
        lora.append("    task_type='CAUSAL_LM',")
        lora.append(")")
        cells.append({"cell_type": "code", "source": "\n".join(lora)})

    trainer = [
        "# ht.callback() keeps the local metrics pane authoritative no matter what",
        "# report_to is set to — an offline chart that always works, plus whichever",
        "# tracker you picked.",
        "trainer = SFTTrainer(",
        f"    model={_literal(recipe.base_model)},",
        "    train_dataset=dataset,",
        "    args=config,",
    ]
    if recipe.use_lora:
        trainer.append("    peft_config=peft_config,")
    trainer += [
        "    callbacks=[ht.callback()],",
        ")",
        "trainer.train()",
    ]
    cells.append({"cell_type": "code", "source": "\n".join(trainer)})

    cells.append(
        {
            "cell_type": "code",
            "source": "\n".join(
                [
                    "trainer.save_model()",
                    "# The checkpoint under output_dir is what 'Convert to GGUF' converts,",
                    "# which is how a model you trained ends up served by this node.",
                    f"print({_literal(recipe.output_dir)})",
                ]
            ),
        }
    )
    return cells


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
