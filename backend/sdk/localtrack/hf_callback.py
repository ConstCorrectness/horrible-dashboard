"""Hugging Face TrainerCallback for LocalTrack integration."""

from __future__ import annotations

import logging
from typing import Any

from backend.sdk.localtrack.client import LocalTrackClient

logger = logging.getLogger("localtrack.hf")

try:
    from transformers import TrainerCallback, TrainerControl, TrainerState, TrainingArguments
except ImportError:
    # Minimal fallback stub so module imports cleanly even if transformers is not present
    class TrainerCallback:  # type: ignore[no-redef]
        pass

    class TrainerControl:  # type: ignore[no-redef]
        pass

    class TrainerState:  # type: ignore[no-redef]
        pass

    class TrainingArguments:  # type: ignore[no-redef]
        pass


class LocalTrackHFCallback(TrainerCallback):
    """Hugging Face `TrainerCallback` that streams training metrics and artifacts to LocalTrack.

    Usage:
        ```python
        from transformers import Trainer
        from backend.sdk.localtrack import LocalTrackHFCallback

        callback = LocalTrackHFCallback(project_name="my-llm-fine-tune", run_name="llama3-lora-run-1")
        trainer = Trainer(..., callbacks=[callback])
        trainer.train()
        ```
    """

    def __init__(
        self,
        project_name: str = "default",
        run_name: str | None = None,
        base_url: str = "http://127.0.0.1:8000",
        config: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.project_name = project_name
        self.run_name = run_name
        self.user_config = config or {}
        self.tags = tags or []
        self.logger = LocalTrackClient(base_url=base_url)

    def on_train_begin(
        self,
        args: Any,
        state: Any,
        control: Any,
        model: Any = None,
        **kwargs: Any,
    ) -> None:
        """Capture hyperparameters, model details, and create the run."""
        config: dict[str, Any] = dict(self.user_config)

        # Extract hyperparameters from TrainingArguments
        if args is not None:
            for attr in [
                "learning_rate",
                "per_device_train_batch_size",
                "per_device_eval_batch_size",
                "gradient_accumulation_steps",
                "num_train_epochs",
                "max_steps",
                "warmup_steps",
                "warmup_ratio",
                "weight_decay",
                "adam_beta1",
                "adam_beta2",
                "adam_epsilon",
                "max_grad_norm",
                "lr_scheduler_type",
                "logging_steps",
                "save_steps",
                "eval_steps",
                "seed",
                "fp16",
                "bf16",
                "optim",
            ]:
                if hasattr(args, attr):
                    val = getattr(args, attr)
                    # Convert enums or non-serializable objects to string
                    if hasattr(val, "value"):
                        val = val.value
                    elif not isinstance(val, (int, float, str, bool, list, dict, type(None))):
                        val = str(val)
                    config[attr] = val

        # Extract model info if available
        system_info: dict[str, Any] = {}
        if model is not None:
            if hasattr(model, "config") and hasattr(model.config, "to_dict"):
                config["model_config"] = model.config.to_dict()
            if hasattr(model, "num_parameters"):
                try:
                    system_info["num_parameters"] = model.num_parameters()
                    system_info["num_trainable_parameters"] = sum(
                        p.numel() for p in model.parameters() if p.requires_grad
                    )
                except Exception:
                    pass

        self.logger.init_run(
            project_name=self.project_name,
            run_name=self.run_name,
            config=config,
            system_info=system_info,
            tags=self.tags,
        )

    def on_log(
        self,
        args: Any,
        state: Any,
        control: Any,
        logs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Asynchronously stream metrics from logs dictionary."""
        if not logs:
            return

        step = state.global_step if state and hasattr(state, "global_step") else 0
        epoch = state.epoch if state and hasattr(state, "epoch") else None

        # Filter and sanitize numeric metrics
        numeric_metrics: dict[str, float | int] = {}
        for k, v in logs.items():
            if isinstance(v, (int, float)):
                numeric_metrics[k] = v

        if numeric_metrics:
            self.logger.log_metrics(metrics=numeric_metrics, step=step, epoch=epoch)

    def on_train_end(
        self,
        args: Any,
        state: Any,
        control: Any,
        **kwargs: Any,
    ) -> None:
        """Finish the run and upload training state / config artifacts."""
        # Try uploading trainer state or output files if available
        if args and hasattr(args, "output_dir"):
            import os
            from pathlib import Path

            out_dir = Path(args.output_dir)
            for fname in ["trainer_state.json", "config.json", "training_args.bin"]:
                fpath = out_dir / fname
                if fpath.is_file():
                    self.logger.log_artifact(str(fpath), artifact_name=fname)

        self.logger.finish_run(status="finished")

    def on_train_error(self, **kwargs: Any) -> None:
        """Handle training failure."""
        self.logger.finish_run(status="failed")
