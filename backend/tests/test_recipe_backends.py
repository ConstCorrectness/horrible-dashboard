"""The recipe-backend layer: every framework, every task it claims.

The property that matters most is boring and total: a backend that declares a task
must emit code for it. A `TaskSpec` the form offers and the emitter cannot fill is
exactly the "option that emits a config the trainer rejects" the module was written
to avoid — it just fails one layer later.
"""

from __future__ import annotations

import ast

import pytest

from backend.modules.training import recipes
from backend.modules.training.backends import BUILTIN
from backend.modules.training.backends.base import all_backends, get_backend

ALL_TASKS = [(b, spec.id) for b in BUILTIN for spec in b.tasks()]
IDS = [f"{b.id}-{task}" for b, task in ALL_TASKS]


def _unvalidated() -> recipes.Introspection:
    """No venv was reachable — every field is emitted hopefully and labelled."""
    return recipes.Introspection(error="no venv")


def _validated(backend, task: str) -> recipes.Introspection:
    """The venv accepts exactly what this backend asks for."""
    targets = backend.probe_classes(task)
    return recipes.Introspection(
        available=bool(targets),
        accepted={
            target: [f.name for f in backend.fields(task, True) if f.target == target]
            for target in targets
        },
        libraries={t: ref.partition(":")[0] for t, ref in targets.items()},
        versions={"trl": "0.30.0", "peft": "0.19.0"},
    )


def _code(cells: list[dict[str, str]]) -> str:
    return "\n".join(c["source"] for c in cells if c["cell_type"] == "code")


@pytest.mark.parametrize(("backend", "task"), ALL_TASKS, ids=IDS)
def test_every_declared_task_emits_parseable_python(backend, task) -> None:
    recipe = recipes.Recipe(
        backend=backend.id,
        task=task,
        base_model="Qwen/Qwen3-0.6B",
        dataset="trl-lib/Capybara",
        values=recipes.defaults(backend.id, task),
    )
    cells = backend.materialize(recipe, _validated(backend, task), None)
    assert cells, "a backend must emit at least a header"
    assert cells[0]["cell_type"] == "markdown"
    ast.parse(_code(cells))


@pytest.mark.parametrize(("backend", "task"), ALL_TASKS, ids=IDS)
def test_every_task_parses_unvalidated_too(backend, task) -> None:
    """The path taken on a fresh project, before anything is installed."""
    recipe = recipes.Recipe(
        backend=backend.id,
        task=task,
        base_model="m",
        dataset="d/s",
        values=recipes.defaults(backend.id, task),
    )
    ast.parse(_code(backend.materialize(recipe, _unvalidated(), None)))


@pytest.mark.parametrize(("backend", "task"), ALL_TASKS, ids=IDS)
def test_every_task_declares_dataset_formats_that_exist(backend, task) -> None:
    """A task whose formats no detector produces can never be satisfied, and the
    refusal would blame the user's dataset."""
    from backend.modules.datasets import formats

    spec = next(s for s in backend.tasks() if s.id == task)
    assert spec.formats, f"{backend.id}/{task} names no dataset shape"
    for fmt in spec.formats:
        assert fmt in formats.FORMATS


@pytest.mark.parametrize(("backend", "task"), ALL_TASKS, ids=IDS)
def test_probe_classes_are_module_colon_class(backend, task) -> None:
    for target, ref in backend.probe_classes(task).items():
        module, sep, name = ref.partition(":")
        assert sep and module and name, f"{backend.id}/{task}/{target} = {ref!r}"


def test_the_generalized_probe_still_drops_an_unsupported_field() -> None:
    """The safety net every new task inherits. A field the installed class does not
    accept is left out of the code and named in the header — not emitted hopefully."""
    backend = get_backend("trl")
    accepted = [f.name for f in backend.fields("dpo", True) if f.target == "config"]
    accepted.remove("beta")
    intro = recipes.Introspection(
        available=True,
        accepted={"config": accepted, "lora": ["r", "lora_alpha"]},
        libraries={"config": "trl", "lora": "peft"},
    )
    recipe = recipes.Recipe(
        backend="trl", task="dpo", values=recipes.defaults("trl", "dpo")
    )
    cells = backend.materialize(recipe, intro, None)
    code = _code(cells)
    assert "beta=" not in code
    assert "# beta: dropped" in code
    assert "`beta`" in cells[0]["source"]


def test_a_renamed_field_is_still_renamed_for_a_new_task() -> None:
    """`max_length` was `max_seq_length`; that alias must apply to DPO too, not
    only to the task the catalog was originally written for."""
    backend = get_backend("trl")
    names = [f.name for f in backend.fields("dpo", True) if f.target == "config"]
    names = [n for n in names if n != "max_length"] + ["max_seq_length"]
    intro = recipes.Introspection(
        available=True,
        accepted={"config": names, "lora": ["r"]},
        libraries={"config": "trl", "lora": "peft"},
        versions={"trl": "0.9.0"},
    )
    recipe = recipes.Recipe(
        backend="trl", task="dpo", values=recipes.defaults("trl", "dpo")
    )
    code = _code(backend.materialize(recipe, intro, None))
    assert "max_seq_length=" in code
    assert "max_length=" not in code


def test_each_trl_task_emits_its_own_config_and_trainer() -> None:
    """DPO is not a variant of SFTConfig. Emitting the wrong pair is a TypeError
    minutes into a run."""
    backend = get_backend("trl")
    for task, config_class, trainer in (
        ("sft", "SFTConfig", "SFTTrainer"),
        ("dpo", "DPOConfig", "DPOTrainer"),
        ("kto", "KTOConfig", "KTOTrainer"),
        ("reward", "RewardConfig", "RewardTrainer"),
        ("grpo", "GRPOConfig", "GRPOTrainer"),
    ):
        recipe = recipes.Recipe(
            backend="trl", task=task, values=recipes.defaults("trl", task)
        )
        code = _code(backend.materialize(recipe, _validated(backend, task), None))
        assert f"from trl import {config_class}, {trainer}" in code
        assert f"config = {config_class}(" in code


def test_grpo_ships_a_reward_placeholder_that_says_it_is_one() -> None:
    """GRPO reinforces whatever the reward scores. A plausible-looking one supplied
    silently would produce runs optimising something nobody chose."""
    backend = get_backend("trl")
    recipe = recipes.Recipe(
        backend="trl", task="grpo", values=recipes.defaults("trl", "grpo")
    )
    code = _code(backend.materialize(recipe, _validated(backend, "grpo"), None))
    assert "PLACEHOLDER" in code
    assert "reward_funcs=[reward_length]" in code


def test_a_reward_model_uses_a_sequence_classification_lora_head() -> None:
    backend = get_backend("trl")
    recipe = recipes.Recipe(
        backend="trl", task="reward", values=recipes.defaults("trl", "reward")
    )
    code = _code(backend.materialize(recipe, _validated(backend, "reward"), None))
    assert "task_type='SEQ_CLS'" in code


def test_unsloth_passes_the_model_object_and_patches_lora_once() -> None:
    """Two traps at once: passing the model NAME loads a second unoptimised copy,
    and passing peft_config as well applies the adapter twice."""
    backend = get_backend("unsloth")
    recipe = recipes.Recipe(
        backend="unsloth",
        task="sft",
        base_model="unsloth/Qwen3-0.6B",
        values=recipes.defaults("unsloth", "sft"),
    )
    code = _code(backend.materialize(recipe, _validated(backend, "sft"), None))
    assert "FastLanguageModel.from_pretrained(" in code
    assert "model=model," in code
    assert "model='unsloth/Qwen3-0.6B'" not in code
    assert "get_peft_model(" in code
    assert "peft_config=" not in code


def test_unsloth_sets_max_seq_length_at_load_time() -> None:
    """RoPE scaling is chosen by the loader; setting it only in SFTConfig
    truncates to the loader's default."""
    backend = get_backend("unsloth")
    recipe = recipes.Recipe(
        backend="unsloth",
        task="sft",
        values={**recipes.defaults("unsloth", "sft"), "max_length": 4096},
    )
    code = _code(backend.materialize(recipe, _validated(backend, "sft"), None))
    loader = code[code.index("from_pretrained(") : code.index("dtype=None")]
    assert "max_seq_length=4096" in loader


def test_unsloth_reuses_trls_catalog_rather_than_copying_it() -> None:
    """A second copy is a second place to fix the warmup unit trap."""
    trl_names = {f.name for f in get_backend("trl").fields("sft", True)}
    unsloth_names = {f.name for f in get_backend("unsloth").fields("sft", True)}
    assert trl_names < unsloth_names
    assert {"load_in_4bit", "use_rslora"} <= unsloth_names


def test_unsloth_reports_all_three_hardware_states() -> None:
    """Found a card / looked and found none / could not ask. Saying "no GPU
    detected" when nvidia-smi merely was not on PATH is the failure the hardware
    module exists to prevent."""
    from backend.modules.hardware.probe import Accelerator, Profile

    backend = get_backend("unsloth")

    def profile(accelerators=(), notes=()):
        return Profile(
            os="windows",
            arch="x86_64",
            cpu_count=8,
            ram_mb=32000,
            ram_exact=True,
            accelerators=tuple(accelerators),
            notes=tuple(notes),
            probed_at=0.0,
        )

    cuda = Accelerator("cuda", "RTX 4080", 16000, False, True, "nvidia-smi")
    assert backend._hardware_note(profile([cuda])) == ""

    absent = backend._hardware_note(profile())
    assert "none was found" in absent

    from backend.modules.hardware.probe import ProbeNote

    unknown = backend._hardware_note(
        profile(notes=[ProbeNote("cuda", "nvidia-smi not on PATH")])
    )
    assert "could not" in unknown.lower()
    assert unknown != absent


def test_pretraining_declares_itself_not_a_fine_tune_and_single_node() -> None:
    for backend in (get_backend("torchtitan"), get_backend("nanotron")):
        warnings = backend.check("pretrain", None)
        assert any("not a faster" in w or "random weights" in w for w in warnings)
        assert any("Multi-node" in w for w in warnings)


def test_pretraining_hides_lora_rather_than_offering_a_dead_toggle() -> None:
    for backend in (get_backend("torchtitan"), get_backend("nanotron")):
        spec = next(s for s in backend.tasks() if s.id == "pretrain")
        assert spec.supports_lora is False
        assert not [f for f in backend.fields("pretrain", True) if f.target == "lora"]


def test_pretraining_emits_a_config_file_and_a_torchrun_launch() -> None:
    for backend_id, marker in (("torchtitan", "[job]"), ("nanotron", "parallelism:")):
        backend = get_backend(backend_id)
        recipe = recipes.Recipe(
            backend=backend_id,
            task="pretrain",
            values=recipes.defaults(backend_id, "pretrain"),
        )
        code = _code(backend.materialize(recipe, _unvalidated(), None))
        assert marker in code
        assert "torch.distributed.run" in code
        assert "--nnodes=1" in code


def test_an_unknown_backend_falls_back_rather_than_failing_to_render() -> None:
    """A plugin was uninstalled. The form still has to open so the user can change
    the backend."""
    recipe = recipes.Recipe(backend="ghost", task="sft")
    cells = recipes.materialize(recipe, _unvalidated())
    assert "from trl import SFTConfig" in _code(cells)


def test_builtin_backends_win_id_conflicts() -> None:
    from backend.sdk.registry import registry

    class Impostor:
        id = "trl"
        label = "Impostor"

    registry.recipe_backends["trl"] = Impostor()
    try:
        assert get_backend("trl").label == "TRL + PEFT"
    finally:
        registry.recipe_backends.clear()


def test_a_plugin_backend_is_listed_alongside_the_builtins() -> None:
    from backend.modules.training.backends.base import TaskSpec
    from backend.sdk.registry import registry

    class Extra:
        id = "extra"
        label = "Extra"
        blurb = ""

        def tasks(self):
            return (TaskSpec("odd", "Odd", "", ("raw_text",)),)

    registry.recipe_backends["extra"] = Extra()
    try:
        assert "extra" in all_backends()
        assert ("extra", "odd") in [(t["backend"], t["id"]) for t in recipes.tasks()]
    finally:
        registry.recipe_backends.clear()
