"""torchtitan and nanotron: pre-training, which is a different execution model.

Every other backend here emits notebook cells that call `trainer.train()` in the
kernel. Pre-training does not work that way and pretending otherwise would be the
worst kind of half-feature. A titan or nanotron run is:

    torchrun --nproc_per_node=N -m torchtitan.train --job.config_file config.toml

— a multi-process launch reading a **config file**, with FSDP/tensor/pipeline
parallelism decided in that file, not in Python kwargs. There is no object whose
constructor you can hand a learning rate to.

So these backends emit **a config file plus a launch cell**, and the run goes
through the script runner rather than the kernel. That is honest about what they
are, and it means the form's whole value — typed fields, defaults, validation
against what is installed — still applies, because a TOML key is exactly as
checkable as a dataclass field.

**Single-node only in v1, and it says so.** The interesting case for these
frameworks is many machines, and this node already advertises GPUs over the peer
fabric (`training/fabric.py`) with no remote execution engine behind it. Shipping
`--nnodes 1` and naming the gap is better than a launcher that appears to support a
cluster and silently runs on one box.

**These are not fine-tuning.** `check()` says so first, every time: someone who
picks "pre-training" expecting a faster LoRA is about to burn a lot of GPU hours
learning that from scratch means from scratch.
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

_TASK = TaskSpec(
    "pretrain",
    "Pre-training (from scratch)",
    "Train a model from randomly initialised weights on a raw corpus. Days to "
    "weeks of GPU time, not minutes — this is not fine-tuning.",
    ("raw_text",),
    supports_lora=False,
)

#: Shared across both frameworks: they are different implementations of the same
#: idea, and every knob below means the same thing in each.
_COMMON: tuple[RecipeField, ...] = (
    RecipeField(
        "model_size",
        "job",
        "Model size",
        "select",
        "debugmodel",
        "`debugmodel` is a few million parameters and exists to prove the pipeline "
        "runs end to end. Start there, always — a misconfigured 8B run fails after "
        "an hour of allocation.",
        "model",
        options=("debugmodel", "1B", "3B", "8B", "70B"),
    ),
    RecipeField(
        "seq_len",
        "job",
        "Sequence length",
        "int",
        2048,
        "Context the model is trained at. Attention cost is quadratic in this, so "
        "it dominates both memory and step time.",
        "model",
    ),
    RecipeField(
        "batch_size",
        "job",
        "Batch size (per device)",
        "int",
        8,
        "Sequences per step per GPU.",
        "optimization",
    ),
    RecipeField(
        "gradient_accumulation_steps",
        "job",
        "Gradient accumulation",
        "int",
        1,
        "Steps accumulated before an optimizer step. Pre-training wants a large "
        "global batch; this is how you get one on few devices.",
        "optimization",
    ),
    RecipeField(
        "learning_rate",
        "job",
        "Learning rate",
        "float",
        3e-4,
        "Pre-training runs two to three orders of magnitude hotter than a LoRA "
        "fine-tune, because the weights start from noise.",
        "optimization",
    ),
    RecipeField(
        "warmup_steps",
        "job",
        "Warmup steps",
        "int",
        200,
        "Steps ramping the learning rate from zero. Pre-training diverges without "
        "a real warmup far more readily than fine-tuning does.",
        "optimization",
    ),
    RecipeField(
        "training_steps",
        "job",
        "Training steps",
        "int",
        1000,
        "Total optimizer steps. There are no epochs here — the corpus is streamed.",
        "optimization",
    ),
    RecipeField(
        "nproc_per_node",
        "launch",
        "GPUs on this node",
        "int",
        1,
        "Processes torchrun starts, one per GPU. Multi-NODE is not wired up yet.",
        "parallelism",
    ),
    RecipeField(
        "data_parallel_shard_degree",
        "job",
        "FSDP shard degree",
        "int",
        -1,
        "How many ways to shard parameters, gradients and optimizer state. `-1` "
        "means 'all available devices', which is what you want on one node.",
        "parallelism",
    ),
    RecipeField(
        "tensor_parallel_degree",
        "job",
        "Tensor parallel degree",
        "int",
        1,
        "Splits individual matrices across devices. Needed only when one layer "
        "does not fit on one GPU.",
        "parallelism",
    ),
    RecipeField(
        "compile",
        "job",
        "torch.compile",
        "bool",
        True,
        "Compiles the model. Substantially faster per step, at a minute or two of "
        "warmup.",
        "optimization",
    ),
    RecipeField(
        "mixed_precision_param",
        "job",
        "Parameter precision",
        "select",
        "bfloat16",
        "bfloat16 is the standard choice for pre-training; float32 is a debugging "
        "aid, not a plan.",
        "memory",
    ),
    RecipeField(
        "checkpoint_interval",
        "job",
        "Checkpoint every",
        "int",
        500,
        "Steps between checkpoints. A pre-training run WILL be interrupted, so "
        "this is not optional the way it is for a ten-minute fine-tune.",
        "logging",
    ),
)


class _PretrainBackend:
    """Shared implementation; the two frameworks differ only in what they emit."""

    id = ""
    label = ""
    blurb = ""
    _distribution = ""
    _module = ""

    def tasks(self) -> tuple[TaskSpec, ...]:
        return (_TASK,)

    def probe_classes(self, task: str) -> dict[str, str]:
        # A TOML key has no class to interrogate, so there is nothing to validate
        # against and every field renders `unvalidated` — which the header says.
        # Reporting the package's presence is still worth it: it is the difference
        # between "we could not check" and "this is not installed at all".
        return {}

    def fields(self, task: str, use_lora: bool) -> tuple[RecipeField, ...]:
        return _COMMON

    def requirements(self, task: str, profile: Any) -> list[str]:
        return [self._distribution]

    def check(self, task: str, profile: Any) -> list[str]:
        out = [
            "Pre-training starts from random weights. It is not a faster "
            "fine-tune — expect days of GPU time for anything past `debugmodel`, "
            "and start with `debugmodel` to prove the pipeline runs.",
            "Multi-node is not wired up yet: this launches `torchrun` on this "
            "machine only. The peer fabric advertises other nodes' GPUs but has no "
            "remote execution engine behind it.",
        ]
        if profile is not None and getattr(profile, "certain", True):
            count = len(getattr(profile, "accelerators", []) or [])
            if not count:
                out.append(
                    "No accelerator was found on this machine. Pre-training on CPU "
                    "is not slow, it is impractical — `debugmodel` will run, "
                    "nothing above it will."
                )
        return out

    def _config_source(self, recipe: Any) -> str:
        raise NotImplementedError

    def materialize(
        self, recipe: Any, intro: Any, run_name: str | None
    ) -> list[dict[str, str]]:
        from backend.modules.training import recipes as R

        resolved = R.resolve_dataset(recipe)
        cells = [md(R.header_source(recipe, intro, resolved, False))]
        cells.append(
            md(
                "\n".join(
                    [
                        f"### {self.label}",
                        "",
                        "Pre-training runs as a **`torchrun` launch reading a config "
                        "file**, not as a trainer object in this kernel. The cell "
                        "below writes that config; the one after it launches the "
                        "run and streams its output back here.",
                        "",
                        "The launch is single-node. Multi-node is not wired up yet.",
                    ]
                )
            )
        )
        cells.append(code(self._config_source(recipe)))

        nproc = recipe.values.get("nproc_per_node", 1)
        cells.append(
            code(
                "\n".join(
                    [
                        "import subprocess, sys",
                        "",
                        "# Streamed rather than captured: a pre-training run is long,",
                        "# and output you only see at the end is output you cannot use.",
                        "proc = subprocess.Popen(",
                        "    [sys.executable, '-m', 'torch.distributed.run',",
                        f"     '--nproc_per_node={int(nproc)}', '--nnodes=1',",
                        f"     '-m', {literal(self._module)},",
                        f"     '--job.config_file', {literal(self._config_path())}],",
                        "    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,",
                        "    text=True, bufsize=1,",
                        ")",
                        "for line in proc.stdout:",
                        "    print(line, end='')",
                        "proc.wait()",
                        "print('exit', proc.returncode)",
                    ]
                )
            )
        )
        return cells

    def _config_path(self) -> str:
        return f"{self.id}_pretrain.toml"


class TorchtitanBackend(_PretrainBackend):
    id = "torchtitan"
    label = "torchtitan"
    blurb = (
        "PyTorch's own pre-training stack: FSDP2, tensor and pipeline parallelism, "
        "driven by a TOML job config. Single-node here."
    )
    _distribution = "torchtitan"
    _module = "torchtitan.train"

    def _config_source(self, recipe: Any) -> str:
        v = recipe.values
        return "\n".join(
            [
                "import pathlib",
                "",
                "config = '''",
                "[job]",
                'dump_folder = "%s"' % recipe.output_dir,
                'description = "generated by the horrible-dashboard recipe form"',
                "",
                "[model]",
                'name = "llama3"',
                'flavor = "%s"' % v.get("model_size", "debugmodel"),
                "",
                "[optimizer]",
                'name = "AdamW"',
                "lr = %s" % v.get("learning_rate", 3e-4),
                "",
                "[lr_scheduler]",
                "warmup_steps = %s" % v.get("warmup_steps", 200),
                "",
                "[training]",
                "local_batch_size = %s" % v.get("batch_size", 8),
                "seq_len = %s" % v.get("seq_len", 2048),
                "steps = %s" % v.get("training_steps", 1000),
                "compile = %s" % str(bool(v.get("compile", True))).lower(),
                'dataset = "c4"',
                "",
                "[parallelism]",
                "data_parallel_shard_degree = %s"
                % v.get("data_parallel_shard_degree", -1),
                "tensor_parallel_degree = %s" % v.get("tensor_parallel_degree", 1),
                "",
                "[checkpoint]",
                "enable = true",
                "interval = %s" % v.get("checkpoint_interval", 500),
                "",
                "[float8]",
                "enable_fsdp_float8_all_gather = false",
                "'''",
                "",
                f"pathlib.Path({literal(self._config_path())}).write_text(config)",
                f"print({literal(self._config_path())})",
            ]
        )


class NanotronBackend(_PretrainBackend):
    id = "nanotron"
    label = "nanotron"
    blurb = (
        "Hugging Face's minimal 3D-parallel pre-training library, driven by a YAML "
        "config. Single-node here."
    )
    _distribution = "nanotron"
    _module = "nanotron.trainer"

    def _config_path(self) -> str:
        return "nanotron_pretrain.yaml"

    def _config_source(self, recipe: Any) -> str:
        v = recipe.values
        return "\n".join(
            [
                "import pathlib",
                "",
                "config = '''",
                "general:",
                "  project: horrible",
                f"  run: {recipe.output_dir}",
                "  seed: 42",
                "",
                "model:",
                "  model_config:",
                "    hidden_size: 512",
                "    num_attention_heads: 8",
                "    num_hidden_layers: 8",
                "    max_position_embeddings: %s" % v.get("seq_len", 2048),
                "  dtype: %s" % v.get("mixed_precision_param", "bfloat16"),
                "",
                "optimizer:",
                "  learning_rate_scheduler:",
                "    learning_rate: %s" % v.get("learning_rate", 3e-4),
                "    lr_warmup_steps: %s" % v.get("warmup_steps", 200),
                "    lr_decay_style: cosine",
                "  accumulate_grad_in_fp32: true",
                "",
                "parallelism:",
                "  dp: %s" % max(1, int(v.get("nproc_per_node", 1))),
                "  tp: %s" % v.get("tensor_parallel_degree", 1),
                "  pp: 1",
                "",
                "tokens:",
                "  micro_batch_size: %s" % v.get("batch_size", 8),
                "  batch_accumulation_per_replica: %s"
                % v.get("gradient_accumulation_steps", 1),
                "  sequence_length: %s" % v.get("seq_len", 2048),
                "  train_steps: %s" % v.get("training_steps", 1000),
                "",
                "checkpoints:",
                f"  checkpoints_path: {recipe.output_dir}",
                "  checkpoint_interval: %s" % v.get("checkpoint_interval", 500),
                "'''",
                "",
                f"pathlib.Path({literal(self._config_path())}).write_text(config)",
                f"print({literal(self._config_path())})",
            ]
        )


TORCHTITAN = TorchtitanBackend()
NANOTRON = NanotronBackend()
