"""Unsloth: the same trl training loop, with a much cheaper model.

Unsloth is not a different trainer — it ends in `SFTTrainer` like the trl backend
does. What it replaces is the *loading*: `FastLanguageModel.from_pretrained` swaps
in fused kernels and 4-bit quantisation, which is where the "2× faster, 70% less
VRAM" comes from. So this backend **reuses trl's field catalog wholesale** and adds
only the knobs that belong to the loader. Duplicating the catalog would mean fixing
the `warmup_ratio`/`warmup_steps` unit trap twice.

Three things it changes about the emitted code, each of which is a real trap:

- **The model is loaded first and passed as an object**, not as a name string. trl
  accepts either; passing the name would silently load a second, unoptimised copy
  and give back none of the speedup while still importing unsloth.
- **LoRA comes from `get_peft_model`, not a `peft_config=` argument.** Unsloth
  patches the adapter in during the wrap. Handing `SFTTrainer` a `LoraConfig` as
  well would apply the adapter twice.
- **`max_seq_length` is set at load time**, because the RoPE scaling is chosen
  there. Setting it only in `SFTConfig` truncates to the loader's default.

`check()` reads the hardware probe, and honours its three states: found a card,
looked and found none, and *could not ask*. The last one matters here — unsloth
requires a CUDA GPU, and saying "no GPU detected" when `nvidia-smi` merely was not
on PATH is exactly the failure the hardware module exists to prevent.
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
from backend.modules.training.backends.trl_backend import BACKEND as _TRL

_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "sft",
        "Supervised fine-tuning (Unsloth)",
        "The same SFT run, on fused kernels and 4-bit weights: roughly twice as "
        "fast and a fraction of the VRAM. Needs an NVIDIA card.",
        ("chatml", "sharegpt", "alpaca", "raw_text"),
    ),
    TaskSpec(
        "dpo",
        "Direct preference optimisation (Unsloth)",
        "Preference training with the same memory savings. Needs a preference "
        "dataset and an NVIDIA card.",
        ("preference",),
    ),
)

_LOADER: tuple[RecipeField, ...] = (
    RecipeField(
        "load_in_4bit",
        "model",
        "Load in 4-bit",
        "bool",
        True,
        "QLoRA: quantise the frozen base weights to 4 bits. The single biggest "
        "memory saving, and why a 14B fine-tune fits on a 16GB card.",
        "memory",
    ),
    RecipeField(
        "use_gradient_checkpointing",
        "model",
        "Gradient checkpointing",
        "select",
        "unsloth",
        "`unsloth` is their own implementation and saves more than the stock one; "
        "`True` is transformers'; `False` is fastest and uses the most memory.",
        "memory",
        options=("unsloth", "True", "False"),
    ),
    RecipeField(
        "use_rslora",
        "lora",
        "Rank-stabilised LoRA",
        "bool",
        False,
        "Scales the adapter by sqrt(rank) instead of rank. Usually better above "
        "rank 32, and roughly neutral below it.",
        "lora",
    ),
)


class UnslothBackend:
    id = "unsloth"
    label = "Unsloth"
    blurb = (
        "Fused kernels and 4-bit QLoRA over the same trl trainer: about twice as "
        "fast on much less VRAM. NVIDIA only."
    )

    def tasks(self) -> tuple[TaskSpec, ...]:
        return _TASKS

    def probe_classes(self, task: str) -> dict[str, str]:
        # The config and adapter classes are still trl's and peft's — only the
        # loader is unsloth's, and it is a function rather than a config class, so
        # there is nothing there to introspect.
        return _TRL.probe_classes(task)

    def fields(self, task: str, use_lora: bool) -> tuple[RecipeField, ...]:
        base = _TRL.fields(task, use_lora)
        extra = _LOADER if use_lora else tuple(f for f in _LOADER if f.target != "lora")
        return base + extra

    def requirements(self, task: str, profile: Any) -> list[str]:
        return ["unsloth", "trl", "peft", "transformers", "datasets", "accelerate"]

    def check(self, task: str, profile: Any) -> list[str]:
        out = list(_TRL.check(task, profile))
        out.append(self._hardware_note(profile))
        return [note for note in out if note]

    def _hardware_note(self, profile: Any) -> str:
        """Three states, never two. See the module docstring."""
        if profile is None:
            return ""
        if not getattr(profile, "certain", True):
            note = getattr(profile, "note", "") or "the GPU probe could not run"
            return (
                f"Could not determine what accelerator this machine has ({note}), so "
                "whether Unsloth will import here is unknown. It requires an NVIDIA "
                "card; if you know you have one, install and run it."
            )
        # `kind` is the probe's own vocabulary (cuda/rocm/metal/vulkan). Matching
        # on the device *name* instead would call an NVIDIA card found only over
        # Vulkan a CUDA machine, which it is not for unsloth's purposes.
        if any(
            getattr(a, "kind", "") == "cuda"
            for a in (getattr(profile, "accelerators", []) or [])
        ):
            return ""
        return (
            "Unsloth requires an NVIDIA GPU and none was found on this machine, so "
            "the import will fail. Use the TRL backend instead — it runs on CPU, "
            "Apple silicon and AMD, just far more slowly."
        )

    def materialize(
        self, recipe: Any, intro: Any, run_name: str | None
    ) -> list[dict[str, str]]:
        from backend.modules.training import recipes as R

        task = recipe.task if recipe.task in ("sft", "dpo") else "sft"
        config_class = "SFTConfig" if task == "sft" else "DPOConfig"
        trainer_class = "SFTTrainer" if task == "sft" else "DPOTrainer"
        resolved = R.resolve_dataset(recipe)
        reshape, text_field = R._reshape_cell(resolved, task)

        cells = [md(R.header_source(recipe, intro, resolved, bool(reshape)))]

        cells.append(
            code(
                "\n".join(
                    [
                        "import horrible_train as ht",
                        "from unsloth import FastLanguageModel",
                        "from datasets import load_dataset",
                        f"from trl import {config_class}, {trainer_class}",
                        "",
                        R.dataset_call(resolved),
                        "dataset",
                    ]
                )
            )
        )

        if reshape:
            cells.append(code(reshape))

        # `max_seq_length` at load time, not only in the config: RoPE scaling is
        # chosen here, and setting it later truncates to the loader's default.
        max_length = recipe.values.get("max_length", 1024)
        loader = [
            "# Unsloth patches the model as it loads it; passing the model NAME to",
            "# the trainer instead would quietly load a second, unoptimised copy.",
            "model, tokenizer = FastLanguageModel.from_pretrained(",
            f"    model_name={literal(recipe.base_model)},",
            f"    max_seq_length={literal(max_length)},",
        ]
        loader += R.kwargs_for(recipe, "model", intro)
        loader += ["    dtype=None,  # None = pick bf16/fp16 for this card", ")"]
        cells.append(code("\n".join(loader)))

        if recipe.use_lora:
            lora = [
                "# The adapter is patched in here, so no peft_config goes to the",
                "# trainer below — passing both applies LoRA twice.",
                "model = FastLanguageModel.get_peft_model(",
                "    model,",
            ]
            for recipe_field in R.fields_for("lora", self.id, task, True):
                resolved_field = R.resolve(recipe_field, intro)
                value = recipe.values.get(recipe_field.name, recipe_field.default)
                name = recipe_field.name
                if name == "use_rslora":
                    lora.append(f"    use_rslora={literal(bool(value))},")
                elif resolved_field.emit is not None:
                    lora.append(f"    {resolved_field.emit}={literal(value)},")
            lora += [f"    max_seq_length={literal(max_length)},", ")"]
            cells.append(code("\n".join(lora)))

        config = [f"config = {config_class}("]
        config.append(f"    output_dir={literal(recipe.output_dir)},")
        config += R.kwargs_for(recipe, "config", intro)
        field_name = text_field or recipe.text_field
        if task == "sft" and field_name and field_name != "text":
            config.append(f"    dataset_text_field={literal(field_name)},")
        config.append(f"    report_to={literal(R.report_to(recipe))},")
        config.append(")")
        cells.append(code("\n".join(config)))

        callback = (
            f"ht.callback(name={literal(run_name)})" if run_name else "ht.callback()"
        )
        cells.append(
            code(
                "\n".join(
                    [
                        f"trainer = {trainer_class}(",
                        "    model=model,",
                        "    processing_class=tokenizer,",
                        "    train_dataset=dataset,",
                        "    args=config,",
                        f"    callbacks=[{callback}],",
                        ")",
                        "trainer.train()",
                    ]
                )
            )
        )
        cells.append(
            code(
                "\n".join(
                    [
                        "trainer.save_model()",
                        "# `save_pretrained_gguf` exists too, but 'Convert to GGUF' in this",
                        "# app converts the checkpoint with llama.cpp's own converter at the",
                        "# tag of the server build that will serve it, and records lineage.",
                        f"print({literal(recipe.output_dir)})",
                    ]
                )
            )
        )
        return cells


BACKEND = UnslothBackend()
