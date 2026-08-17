"""LocalTrack demonstration script.

Simulates multiple experiment runs (e.g. baseline-model, celestial-lake-3, lora-r16-fp16)
logging realistic noisy loss curves, eval accuracy, learning rate schedules,
and uploading sample configuration artifacts.
"""

from __future__ import annotations

import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path

# Add repo root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.sdk.localtrack import LocalTrackClient
from backend.modules.localtrack import store
from backend.modules.localtrack.models import MetricLogItem


def run_simulation(
    project_name: str = "horrible-sft",
    run_name: str = "celestial-lake-3",
    total_steps: int = 150,
    base_lr: float = 2e-4,
    initial_loss: float = 3.2,
    noise_level: float = 0.15,
) -> None:
    print(f"\n[*] Starting simulation for run: {run_name} in project: {project_name}")
    
    # Check if backend server is reachable, else write directly to store
    use_direct_store = False
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:8000/api/localtrack/projects", timeout=1)
    except Exception:
        use_direct_store = True

    config = {
        "model_name_or_path": "meta-llama/Meta-Llama-3-8B",
        "learning_rate": base_lr,
        "per_device_train_batch_size": 4,
        "gradient_accumulation_steps": 4,
        "num_train_epochs": 3,
        "max_steps": total_steps,
        "warmup_steps": int(total_steps * 0.1),
        "weight_decay": 0.01,
        "lr_scheduler_type": "cosine",
        "fp16": False,
        "bf16": True,
        "optim": "adamw_torch",
        "seed": random.randint(1, 1000),
    }

    system_info = {
        "gpu": "NVIDIA GeForce RTX 4090",
        "vram_gb": 24.0,
        "cpu_count": 16,
        "ram_gb": 64.0,
    }

    warmup_steps = config["warmup_steps"]
    loss = initial_loss

    if use_direct_store:
        project = store.create_project(project_id=project_name, name=project_name)
        run = store.create_run(
            run_id=None,
            project_id=project.id,
            name=run_name,
            config=config,
            system_info=system_info,
            tags=["sft", "llama-3", "lora"],
        )
        run_id = run.id

        batch: list[MetricLogItem] = []
        for step in range(1, total_steps + 1):
            if step < warmup_steps:
                lr = base_lr * (step / max(1, warmup_steps))
            else:
                progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
                lr = base_lr * 0.5 * (1.0 + math.cos(progress * math.pi))

            decay = math.exp(-step / (total_steps * 0.35))
            spike = 0.6 if random.random() < 0.03 else 0.0
            noise = (random.random() - 0.5) * noise_level + spike
            loss = 0.45 + (initial_loss - 0.45) * decay + noise
            epoch = round(step / (total_steps / 3.0), 3)

            now_ts = time.time()
            metrics: dict[str, float | int] = {
                "train/loss": round(max(0.1, loss), 4),
                "train/learning_rate": lr,
                "train/epoch": epoch,
                "train/grad_norm": round(random.uniform(0.3, 1.8) + (spike * 2.0), 3),
            }

            if step % 15 == 0 or step == total_steps:
                eval_acc = min(0.96, 0.40 + 0.55 * (1.0 - math.exp(-step / (total_steps * 0.4))) + (random.random() - 0.5) * 0.03)
                metrics["eval/loss"] = round(loss * 1.05 + (random.random() - 0.5) * 0.05, 4)
                metrics["eval/accuracy"] = round(eval_acc, 4)

            batch.append(MetricLogItem(run_id=run_id, step=step, epoch=epoch, timestamp=now_ts, metrics=metrics))

        store.ingest_metrics(batch)
        store.save_artifact(run_id, "trainer_state.json", b'{"trained_epochs": 3, "best_eval_loss": 0.482, "status": "completed"}')
        store.save_artifact(run_id, "config.json", b'{"architectures": ["LlamaForCausalLM"], "vocab_size": 128256, "hidden_size": 4096}')
        store.update_run(run_id, status="finished")
        print(f"[+] Populated direct database run: {run_name} (ID: {run_id})")
        return

    logger = LocalTrackClient(base_url="http://127.0.0.1:8000", batch_size=20)
    run_id = logger.init_run(
        project_name=project_name,
        run_name=run_name,
        config=config,
        system_info=system_info,
        tags=["sft", "llama-3", "lora"],
    )

    for step in range(1, total_steps + 1):
        if step < warmup_steps:
            lr = base_lr * (step / max(1, warmup_steps))
        else:
            progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
            lr = base_lr * 0.5 * (1.0 + math.cos(progress * math.pi))

        decay = math.exp(-step / (total_steps * 0.35))
        spike = 0.6 if random.random() < 0.03 else 0.0
        noise = (random.random() - 0.5) * noise_level + spike
        loss = 0.45 + (initial_loss - 0.45) * decay + noise
        epoch = round(step / (total_steps / 3.0), 3)

        metrics = {
            "train/loss": round(max(0.1, loss), 4),
            "train/learning_rate": lr,
            "train/epoch": epoch,
            "train/grad_norm": round(random.uniform(0.3, 1.8) + (spike * 2.0), 3),
        }

        if step % 15 == 0 or step == total_steps:
            eval_acc = min(0.96, 0.40 + 0.55 * (1.0 - math.exp(-step / (total_steps * 0.4))) + (random.random() - 0.5) * 0.03)
            metrics["eval/loss"] = round(loss * 1.05 + (random.random() - 0.5) * 0.05, 4)
            metrics["eval/accuracy"] = round(eval_acc, 4)

        logger.log_metrics(metrics=metrics, step=step, epoch=epoch)
        time.sleep(0.01)

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        f.write('{"trained_epochs": 3, "best_eval_loss": 0.482, "status": "completed"}')
        trainer_state_path = f.name

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        f.write('{"architectures": ["LlamaForCausalLM"], "vocab_size": 128256, "hidden_size": 4096}')
        config_path = f.name

    try:
        logger.log_artifact(trainer_state_path, "trainer_state.json")
        logger.log_artifact(config_path, "config.json")
    finally:
        Path(trainer_state_path).unlink(missing_ok=True)
        Path(config_path).unlink(missing_ok=True)

    logger.finish_run(status="finished")
    print(f"[+] Completed simulation for run: {run_name} (ID: {run_id})")


if __name__ == "__main__":
    print("[*] LocalTrack Simulation Launcher")
    # Simulate 3 comparative runs
    run_simulation(project_name="horrible-sft", run_name="celestial-lake-3", base_lr=2e-4, initial_loss=3.1)
    run_simulation(project_name="horrible-sft", run_name="baseline-model", base_lr=1e-4, initial_loss=3.5)
    run_simulation(project_name="horrible-sft", run_name="lora-r16-fp16", base_lr=5e-4, initial_loss=2.8)
