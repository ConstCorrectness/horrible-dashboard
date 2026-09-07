"""The recipe-backend contract: one training framework, as a schema and an emitter.

The recipe surface began as one framework (trl) and one task (SFT), hardcoded end
to end: the field catalog named `SFTConfig`, the probe imported `trl`, the emitter
wrote `SFTTrainer`. That was the right first shape and it does not extend — DPO is
not a variant of `SFTConfig`, unsloth loads a model differently, and pre-training is
not a notebook cell at all.

A `RecipeBackend` is the seam. It declares which tasks it can train, which knobs
each takes, and what code those knobs become. Everything *around* it — the form,
the persistence, the notebook write, the sweep — is written once against this
protocol.

**The safety net is inherited, not reimplemented.** `recipes.introspect()` asks a
backend for `probe_classes(task)` and then interrogates *those* classes in the
project venv, so a new task or a whole new framework gets the existing
`ok | renamed | unsupported | unvalidated` resolution for free. A backend that
wrote its own validation would be a backend whose "dropped field" report drifts
from every other one's, which is precisely the failure the original module was
built to prevent.

**Emitters return cells, not text.** `materialize` returns nbformat-ish cell dicts
and `materialize_script` flattens them (`recipes.materialize_script`), so a notebook
run and a sweep point cannot disagree about what a recipe means.

New frameworks plug in two ways: a file in this package (built-in) or a backend
plugin calling `host.add_recipe_backend(backend)` (see `backend.sdk`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RecipeField:
    """One knob, as it is rendered and as it is emitted.

    Lives here rather than in `recipes` so a backend can declare its catalog
    without importing the module that consumes it.
    """

    name: str
    #: Which emitted object this knob belongs to — the backend's own vocabulary
    #: (`sft`, `lora`, `dpo`, `model`, `titan`…), matched against `probe_classes`.
    target: str
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


@dataclass
class TaskSpec:
    """One thing a backend can train, as the form needs to describe it."""

    id: str
    label: str
    blurb: str
    #: Dataset shapes this task can eat, from `datasets.formats.TASK_FORMATS`.
    #: Named here so the form can refuse an incompatible dataset *before* the run
    #: rather than after it.
    formats: tuple[str, ...] = ()
    #: True when the task trains a LoRA adapter rather than every weight. A task
    #: that cannot (pre-training) hides the LoRA section entirely instead of
    #: rendering a toggle that does nothing.
    supports_lora: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "blurb": self.blurb,
            "formats": list(self.formats),
            "supportsLora": self.supports_lora,
        }


@runtime_checkable
class RecipeBackend(Protocol):
    """Uniform contract every training framework implements."""

    id: str
    label: str
    blurb: str

    def tasks(self) -> tuple[TaskSpec, ...]:
        """Everything this backend can train."""
        ...

    def probe_classes(self, task: str) -> dict[str, str]:
        """target -> `"module:ClassName"` for the classes this task emits.

        What `introspect()` interrogates in the project venv. A target absent here
        is a target whose fields are emitted unvalidated, which the form says.
        """
        ...

    def fields(self, task: str, use_lora: bool) -> tuple[RecipeField, ...]:
        """The knobs this task renders, in form order."""
        ...

    def requirements(self, task: str, profile: Any) -> list[str]:
        """pip requirement specs this task needs in the project venv.

        `profile` is a `hardware.Profile`, so a backend can ask for a CUDA build on
        a machine that has one and refuse to on a machine that does not.
        """
        ...

    def check(self, task: str, profile: Any) -> list[str]:
        """Warnings to show before the run: hardware, versions, task-shape traps.

        Sentences, not codes — they are rendered verbatim.
        """
        ...

    def materialize(
        self, recipe: Any, intro: Any, run_name: str | None
    ) -> list[dict[str, str]]:
        """The recipe as notebook cells (`{cell_type, source}`)."""
        ...


def md(source: str) -> dict[str, str]:
    return {"cell_type": "markdown", "source": source}


def code(source: str) -> dict[str, str]:
    return {"cell_type": "code", "source": source}


def literal(value: Any) -> str:
    return repr(value)


def all_backends() -> dict[str, RecipeBackend]:
    """Built-ins plus any a backend plugin registered. Built-ins win id conflicts,
    matching `training.providers`, `search.providers` and `datasets.sources`."""
    from backend.modules.training.backends import BUILTIN
    from backend.sdk.registry import registry

    out: dict[str, RecipeBackend] = dict(getattr(registry, "recipe_backends", {}) or {})
    for backend in BUILTIN:
        out[backend.id] = backend
    return out


def get_backend(backend_id: str) -> RecipeBackend:
    found = all_backends().get(backend_id or "trl")
    if found is None:
        known = ", ".join(sorted(all_backends()))
        raise ValueError(f"unknown recipe backend {backend_id!r}. Known: {known}")
    return found


def backend_for_task(task: str) -> RecipeBackend | None:
    """The first backend that claims this task. Used when a recipe names a task but
    no backend, which is every recipe written before backends existed."""
    for backend in all_backends().values():
        if any(spec.id == task for spec in backend.tasks()):
            return backend
    return None
