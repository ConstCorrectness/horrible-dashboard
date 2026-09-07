"""trl + peft: supervised fine-tuning and the post-training family.

The original recipe surface, now one backend among several — and grown from one
task to six. The five new ones are not variations on SFT; each has its own config
class, its own trainer, and its own dataset shape, which is exactly why they were
deliberately absent before ("a task option that emits a config the trainer rejects
is worse than no option") and exactly what makes them safe to add now: the format
detector in `datasets.formats` can refuse an incompatible dataset before the run,
and the generalized probe validates each task's real config class in the project
venv.

The tasks and the traps they carry:

- **sft** — the baseline. Chat or instruction data.
- **cpt** — continued pre-training on raw corpus. Same trainer, but LoRA on
  attention alone teaches very little new knowledge, so the check says so.
- **dpo** — direct preference optimisation. Needs a `preference` dataset, and
  **`beta` is the whole knob**: too high and nothing moves, too low and the model
  drifts off the reference.
- **kto** — like DPO but on unpaired thumbs-up/down data, so it takes a *label*
  column rather than chosen/rejected. Listed against `preference` because that is
  the shape our detector reports for both.
- **reward** — trains a scoring head, not a generator. The output is not a chat
  model, and that surprises people, so `check()` says it.
- **grpo** — online RL. It *generates* during training, so it is far slower per
  step than everything else here and needs a reward function, which the form
  cannot express; the generated cell defines a placeholder you must edit.
"""

from __future__ import annotations

from typing import Any

from backend.modules.training.backends.base import (
    RecipeField,
    TaskSpec,
    code,
    literal,
    md,
)

_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "sft",
        "Supervised fine-tuning",
        "Teach the model to answer the way your examples do. The default, and what "
        "most fine-tunes are.",
        ("chatml", "sharegpt", "alpaca", "raw_text"),
    ),
    TaskSpec(
        "cpt",
        "Continued pre-training",
        "Keep training on raw text to absorb a domain's vocabulary and style. No "
        "instruction format — just a corpus.",
        ("raw_text",),
    ),
    TaskSpec(
        "dpo",
        "Direct preference optimisation",
        "Learn from pairs where one answer is better than another. Needs a "
        "preference dataset; usually run after SFT, not instead of it.",
        ("preference",),
    ),
    TaskSpec(
        "kto",
        "KTO",
        "Preference learning from unpaired good/bad judgements — cheaper to "
        "collect than pairs, since one answer at a time can be labelled.",
        ("preference",),
    ),
    TaskSpec(
        "reward",
        "Reward model",
        "Train a model that SCORES answers rather than writing them. The output is "
        "not a chat model.",
        ("preference",),
    ),
    TaskSpec(
        "grpo",
        "GRPO (online RL)",
        "Generate answers during training and reinforce the ones a reward function "
        "scores highly. Much slower per step, and you must write the reward.",
        ("chatml", "sharegpt", "alpaca"),
    ),
)

#: task -> (config class, trainer class), both in `trl`.
_CLASSES: dict[str, tuple[str, str]] = {
    "sft": ("SFTConfig", "SFTTrainer"),
    "cpt": ("SFTConfig", "SFTTrainer"),
    "dpo": ("DPOConfig", "DPOTrainer"),
    "kto": ("KTOConfig", "KTOTrainer"),
    "reward": ("RewardConfig", "RewardTrainer"),
    "grpo": ("GRPOConfig", "GRPOTrainer"),
}


# --- the shared catalog ------------------------------------------------------
#
# Every trl trainer subclasses `TrainingArguments`, so these apply to all six.
# Keeping one copy is what stops "we fixed the warmup alias for SFT but not DPO".

_COMMON: tuple[RecipeField, ...] = (
    RecipeField(
        "max_length",
        "config",
        "Max sequence length",
        "int",
        1024,
        "Tokens per example; longer examples are truncated. The single biggest "
        "lever on memory after batch size.",
        "data",
        aliases=("max_seq_length",),
    ),
    RecipeField(
        "num_train_epochs",
        "config",
        "Epochs",
        "float",
        1.0,
        "Passes over the dataset.",
        "optimization",
    ),
    RecipeField(
        "per_device_train_batch_size",
        "config",
        "Batch size (per device)",
        "int",
        1,
        "Examples per step per GPU. The first thing to lower when you run out of "
        "memory.",
        "optimization",
    ),
    RecipeField(
        "gradient_accumulation_steps",
        "config",
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
        "config",
        "Learning rate",
        "float",
        2e-4,
        "2e-4 is a common LoRA starting point; a full fine-tune wants roughly two "
        "orders of magnitude less.",
        "optimization",
    ),
    RecipeField(
        "lr_scheduler_type",
        "config",
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
    # no error anywhere.
    RecipeField(
        "warmup_ratio",
        "config",
        "Warmup ratio",
        "float",
        0.03,
        "Fraction of total steps spent ramping the learning rate up from zero. "
        "Removed in transformers 5 in favour of warmup_steps.",
        "optimization",
    ),
    RecipeField(
        "warmup_steps",
        "config",
        "Warmup steps",
        "int",
        0,
        "Steps spent ramping the learning rate up from zero. A count, not a "
        "fraction — the two are not interchangeable.",
        "optimization",
    ),
    RecipeField(
        "weight_decay",
        "config",
        "Weight decay",
        "float",
        0.0,
        "L2 regularization on the optimizer.",
        "optimization",
    ),
    RecipeField(
        "max_grad_norm",
        "config",
        "Gradient clipping",
        "float",
        1.0,
        "Clip gradients to this norm. The usual guard against a loss spike ending "
        "a run.",
        "optimization",
    ),
    RecipeField(
        "optim",
        "config",
        "Optimizer",
        "select",
        "adamw_torch",
        "`adamw_8bit` needs bitsandbytes and roughly halves optimizer memory.",
        "optimization",
        options=("adamw_torch", "adamw_8bit", "adafactor", "sgd"),
    ),
    RecipeField(
        "seed",
        "config",
        "Seed",
        "int",
        42,
        "Fixes shuffling and initialization, so a rerun is a rerun.",
        "optimization",
    ),
    RecipeField(
        "bf16",
        "config",
        "bfloat16",
        "bool",
        True,
        "Half precision with fp32's exponent range. Needs Ampere or newer; on "
        "older cards use fp16.",
        "memory",
    ),
    RecipeField(
        "fp16",
        "config",
        "float16",
        "bool",
        False,
        "Half precision for pre-Ampere cards. Do not enable alongside bf16.",
        "memory",
    ),
    RecipeField(
        "gradient_checkpointing",
        "config",
        "Gradient checkpointing",
        "bool",
        True,
        "Recompute activations in the backward pass instead of storing them: much "
        "less memory, roughly 20-30% slower.",
        "memory",
    ),
    RecipeField(
        "logging_steps",
        "config",
        "Log every",
        "int",
        10,
        "Steps between metric logs. Also the resolution of the live chart.",
        "logging",
    ),
    RecipeField(
        "save_steps",
        "config",
        "Save every",
        "int",
        200,
        "Steps between checkpoints.",
        "logging",
    ),
    RecipeField(
        "save_total_limit",
        "config",
        "Keep checkpoints",
        "int",
        2,
        "Older checkpoints beyond this are deleted. Checkpoints are large.",
        "logging",
    ),
)

_PACKING = RecipeField(
    "packing",
    "config",
    "Pack examples",
    "bool",
    False,
    "Concatenate short examples up to the sequence length instead of padding. "
    "Much faster on short data; wrong if examples must not bleed together.",
    "data",
)

#: Per-task extras, appended to the common catalog.
_EXTRA: dict[str, tuple[RecipeField, ...]] = {
    "sft": (_PACKING,),
    "cpt": (_PACKING,),
    "dpo": (
        RecipeField(
            "beta",
            "config",
            "Beta",
            "float",
            0.1,
            "How hard the model is held to the reference model. The main DPO knob: "
            "too high and nothing moves, too low and it drifts into nonsense.",
            "objective",
        ),
        RecipeField(
            "loss_type",
            "config",
            "Loss",
            "select",
            "sigmoid",
            "`sigmoid` is standard DPO; `ipo` is more robust to noisy pairs; "
            "`hinge` is SLiC-style.",
            "objective",
            options=("sigmoid", "hinge", "ipo", "kto_pair"),
        ),
        RecipeField(
            "max_prompt_length",
            "config",
            "Max prompt length",
            "int",
            512,
            "Tokens reserved for the prompt. The completion gets max_length minus "
            "this, so setting it too high silently truncates answers.",
            "data",
        ),
    ),
    "kto": (
        RecipeField(
            "beta",
            "config",
            "Beta",
            "float",
            0.1,
            "Strength of the KL penalty against the reference model.",
            "objective",
        ),
        RecipeField(
            "desirable_weight",
            "config",
            "Desirable weight",
            "float",
            1.0,
            "Weight on the thumbs-up examples. Raise it when the two labels are "
            "unbalanced — which they usually are.",
            "objective",
        ),
        RecipeField(
            "undesirable_weight",
            "config",
            "Undesirable weight",
            "float",
            1.0,
            "Weight on the thumbs-down examples.",
            "objective",
        ),
    ),
    "reward": (
        RecipeField(
            "center_rewards_coefficient",
            "config",
            "Centre rewards",
            "float",
            0.0,
            "Penalises drift in the average score. Helps when the reward model is "
            "later used as an absolute number rather than a comparison.",
            "objective",
        ),
    ),
    "grpo": (
        RecipeField(
            "num_generations",
            "config",
            "Generations per prompt",
            "int",
            8,
            "Answers sampled per prompt and ranked against each other. The group "
            "size in GRPO, and the main cost multiplier.",
            "objective",
        ),
        RecipeField(
            "max_completion_length",
            "config",
            "Max completion length",
            "int",
            256,
            "Tokens each sampled answer may reach. Every step generates this many "
            "× generations, so it dominates step time.",
            "objective",
        ),
        RecipeField(
            "beta",
            "config",
            "KL coefficient",
            "float",
            0.04,
            "How hard the policy is held to the reference model.",
            "objective",
        ),
        RecipeField(
            "temperature",
            "config",
            "Sampling temperature",
            "float",
            0.9,
            "Diversity of the sampled group. Too low and every generation is the "
            "same answer, so there is nothing to rank.",
            "objective",
        ),
    ),
}

_LORA: tuple[RecipeField, ...] = (
    RecipeField(
        "r",
        "lora",
        "LoRA rank",
        "int",
        16,
        "Capacity of the adapter. 8-16 for style, 32-64 to teach new behaviour.",
        "lora",
    ),
    RecipeField(
        "lora_alpha",
        "lora",
        "LoRA alpha",
        "int",
        32,
        "Scaling. The common convention is twice the rank.",
        "lora",
    ),
    RecipeField(
        "lora_dropout",
        "lora",
        "LoRA dropout",
        "float",
        0.05,
        "Regularization on the adapter.",
        "lora",
    ),
    RecipeField(
        "bias",
        "lora",
        "Train biases",
        "select",
        "none",
        "`none` is the usual choice and the only one that merges cleanly.",
        "lora",
        options=("none", "all", "lora_only"),
    ),
    RecipeField(
        "target_modules",
        "lora",
        "Target modules",
        "text",
        "all-linear",
        "Which layers get an adapter. `all-linear` covers every linear layer and "
        "is the safe default across architectures.",
        "lora",
    ),
)


class TrlBackend:
    id = "trl"
    label = "TRL + PEFT"
    blurb = (
        "Hugging Face's trl: supervised fine-tuning and the post-training family "
        "(DPO, KTO, reward modelling, GRPO), with LoRA adapters via peft."
    )

    def tasks(self) -> tuple[TaskSpec, ...]:
        return _TASKS

    def probe_classes(self, task: str) -> dict[str, str]:
        config_class, _ = _CLASSES.get(task, _CLASSES["sft"])
        return {"config": f"trl:{config_class}", "lora": "peft:LoraConfig"}

    def fields(self, task: str, use_lora: bool) -> tuple[RecipeField, ...]:
        out = _COMMON + _EXTRA.get(task, ())
        return out + _LORA if use_lora else out

    def requirements(self, task: str, profile: Any) -> list[str]:
        # `torch` is deliberately absent: its wheel is chosen by accelerator and
        # OS together, so `envs.install_stack` adds it with the right index URL
        # rather than letting pip pick a CPU build on a machine with a card.
        return ["trl", "peft", "transformers", "datasets", "accelerate"]

    def check(self, task: str, profile: Any) -> list[str]:
        out: list[str] = []
        if task == "reward":
            out.append(
                "A reward model scores answers rather than writing them: the "
                "checkpoint this produces is not a chat model and cannot be served "
                "as one."
            )
        if task == "grpo":
            out.append(
                "GRPO generates during training, so a step costs many times an SFT "
                "step. The generated cell defines a placeholder reward function — "
                "it must be edited before the run means anything."
            )
        if task == "cpt":
            out.append(
                "Continued pre-training with a LoRA on attention alone absorbs very "
                "little new knowledge. Either target the MLP layers too "
                "(`all-linear`, the default here) or train the full model."
            )
        if task in ("dpo", "kto", "reward"):
            out.append(
                "Preference training assumes the model already answers in the right "
                "format. Run it on an SFT checkpoint rather than on a base model."
            )
        return out

    # --- emission ------------------------------------------------------------

    def materialize(
        self, recipe: Any, intro: Any, run_name: str | None
    ) -> list[dict[str, str]]:
        from backend.modules.training import recipes as R

        task = recipe.task if recipe.task in _CLASSES else "sft"
        config_class, trainer_class = _CLASSES[task]
        resolved = R.resolve_dataset(recipe)
        reshape, text_field = R._reshape_cell(resolved, task)

        cells = [md(R.header_source(recipe, intro, resolved, bool(reshape)))]

        imports = [
            "import horrible_train as ht",
            "from datasets import load_dataset",
            f"from trl import {config_class}, {trainer_class}",
        ]
        if recipe.use_lora:
            imports.append("from peft import LoraConfig")
        imports += ["", R.dataset_call(resolved), "dataset"]
        cells.append(code("\n".join(imports)))

        if reshape:
            cells.append(code(reshape))

        config = [f"config = {config_class}("]
        config.append(f"    output_dir={literal(recipe.output_dir)},")
        config += R.kwargs_for(recipe, "config", intro)
        field_name = text_field or recipe.text_field
        if task in ("sft", "cpt") and field_name and field_name != "text":
            # Pointing trl at the original column after a reshape trains on a
            # column that no longer holds what it did. `text` is trl's own default
            # and is left implicit. The preference trainers read `chosen`/
            # `rejected` by convention and take no text-field argument at all.
            config.append(f"    dataset_text_field={literal(field_name)},")
        config.append(f"    report_to={literal(R.report_to(recipe))},")
        config.append(")")
        cells.append(code("\n".join(config)))

        if recipe.use_lora:
            lora = ["peft_config = LoraConfig("]
            lora += R.kwargs_for(recipe, "lora", intro)
            lora.append(
                "    task_type='SEQ_CLS',"
                if task == "reward"
                else "    task_type='CAUSAL_LM',"
            )
            lora.append(")")
            cells.append(code("\n".join(lora)))

        if task == "grpo":
            cells.append(code(_GRPO_REWARD))

        callback = (
            f"ht.callback(name={literal(run_name)})" if run_name else "ht.callback()"
        )
        trainer = [
            "# ht.callback() keeps the local metrics pane authoritative no matter what",
            "# report_to is set to — an offline chart that always works, plus whichever",
            "# tracker you picked.",
            f"trainer = {trainer_class}(",
            f"    model={literal(recipe.base_model)},",
            "    train_dataset=dataset,",
            "    args=config,",
        ]
        if task == "grpo":
            trainer.append("    reward_funcs=[reward_length],")
        if recipe.use_lora:
            trainer.append("    peft_config=peft_config,")
        trainer += [f"    callbacks=[{callback}],", ")", "trainer.train()"]
        cells.append(code("\n".join(trainer)))

        cells.append(
            code(
                "\n".join(
                    [
                        "trainer.save_model()",
                        "# The checkpoint under output_dir is what 'Convert to GGUF' converts,",
                        "# which is how a model you trained ends up served by this node.",
                        f"print({literal(recipe.output_dir)})",
                    ]
                )
            )
        )
        return cells


#: A placeholder, and it says so. GRPO's reward is the experiment — a form cannot
#: express it, and a backend that silently supplied a plausible-looking one would
#: produce runs that optimise something nobody chose.
_GRPO_REWARD = '''def reward_length(completions, **kwargs):
    """PLACEHOLDER — replace this with your actual reward.

    GRPO reinforces whatever this function scores highly, so it *is* the
    experiment. This one rewards answers near 200 characters, which is a stand-in
    for "something measurable", not a useful objective.

    Return one float per completion.
    """
    return [-abs(len(c) - 200) / 200 for c in completions]
'''

BACKEND = TrlBackend()
