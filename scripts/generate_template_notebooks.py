import json
from pathlib import Path
import nbformat as nbf

def create_template_notebooks():
    nb_dir = Path("notebooks")
    nb_dir.mkdir(exist_ok=True)

    # --- 1. Hugging Face Training Notebook ---
    nb1 = nbf.v4.new_notebook()
    nb1.cells = [
        nbf.v4.new_markdown_cell("""# 🚀 Model Training & Fine-Tuning with LocalTrack & Hugging Face

This notebook demonstrates how to train or fine-tune machine learning models with PyTorch and Hugging Face `transformers` while streaming live metrics directly to **LocalTrack**.

### Features:
- Automatic hyperparameter capture (`learning_rate`, `batch_size`, `warmup_steps`, optimizer)
- High-throughput asynchronous metric streaming (`train/loss`, `eval/accuracy`, `learning_rate`)
- Artifact uploads (`config.json`, `trainer_state.json`)"""),
        nbf.v4.new_code_cell("""# 1. Imports and environment setup
import math
import random
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from backend.sdk.localtrack import LocalTrackClient, LocalTrackHFCallback

print(f"PyTorch version: {torch.__version__} | CUDA available: {torch.cuda.is_available()}")"""),
        nbf.v4.new_markdown_cell("""## 2. Initialize LocalTrack Logger Callback

Configure the `LocalTrackHFCallback` to track the experiment under the `llm-finetuning` project."""),
        nbf.v4.new_code_cell("""# Create LocalTrack Hugging Face Callback
callback = LocalTrackHFCallback(
    project_name="llm-finetuning",
    run_name="llama3-lora-r16",
    base_url="http://127.0.0.1:8000",
    tags=["llama-3", "lora", "sft"],
)

print(f"Configured LocalTrack callback for project: {callback.project_name}")"""),
        nbf.v4.new_markdown_cell("""## 3. Simulated Training Loop with Metric Logging

For environments without heavy GPUs, here is the simulated high-speed training loop capturing the exact telemetry emitted by `Trainer`."""),
        nbf.v4.new_code_cell("""# Direct Client Logging Demonstration
client = LocalTrackClient(base_url="http://127.0.0.1:8000")
run_id = client.init_run(
    project_name="llm-finetuning",
    run_name="llama3-lora-r16",
    config={
        "learning_rate": 2e-4,
        "batch_size": 16,
        "lora_rank": 16,
        "lora_alpha": 32,
        "epochs": 3,
    },
    tags=["llama-3", "lora", "sft"],
)

print(f"Started LocalTrack Run ID: {run_id}")

total_steps = 100
for step in range(1, total_steps + 1):
    loss = 2.5 * math.exp(-step / 30.0) + random.uniform(-0.05, 0.05) + 0.35
    lr = 2e-4 * (1.0 - step / total_steps)
    client.log_metrics({
        "train/loss": round(loss, 4),
        "train/learning_rate": lr,
        "train/epoch": round(step / 33.3, 2),
    }, step=step)

client.finish_run(status="finished")
print("✅ Training complete! Open LocalTrack Workspace to view the live curves.")"""),
    ]
    with open(nb_dir / "01_localtrack_huggingface_training.ipynb", "w", encoding="utf-8") as f:
        nbf.write(nb1, f)

    # --- 2. Experiment Analysis Notebook ---
    nb2 = nbf.v4.new_notebook()
    nb2.cells = [
        nbf.v4.new_markdown_cell("""# 📊 LocalTrack Experiment Analysis & Multi-Run Comparison

Query historical experiment runs, fetch downsampled time-series loss curves via LTTB, and analyze hyperparameter correlations."""),
        nbf.v4.new_code_cell("""import pandas as pd
import requests

BASE_URL = "http://127.0.0.1:8000"

# Fetch all projects
projects = requests.get(f"{BASE_URL}/api/localtrack/projects").json()
print("Available Projects:", [p["name"] for p in projects["projects"]])"""),
        nbf.v4.new_markdown_cell("""## 1. Fetch Runs & Compare Summary Metrics"""),
        nbf.v4.new_code_cell("""# List all runs in project
runs_resp = requests.get(f"{BASE_URL}/api/localtrack/runs", params={"project_id": "horrible-sft"}).json()
runs = runs_resp.get("runs", [])

df_runs = pd.DataFrame([
    {
        "id": r["id"],
        "name": r["name"],
        "status": r["status"],
        "lr": r["config"].get("learning_rate"),
        "final_loss": r["summary"].get("train/loss"),
        "eval_acc": r["summary"].get("eval/accuracy"),
        "tags": ", ".join(r["tags"]),
    }
    for r in runs
])

df_runs.sort_values(by="eval_acc", ascending=False) if not df_runs.empty else df_runs"""),
        nbf.v4.new_markdown_cell("""## 2. Query Downsampled Metric Time-Series (LTTB)"""),
        nbf.v4.new_code_cell("""if len(runs) > 0:
    run_ids = [r["id"] for r in runs[:3]]
    query_payload = {
        "run_ids": run_ids,
        "keys": ["train/loss", "eval/accuracy"],
        "max_points": 100,
        "smoothing": 0.3,
    }
    metrics_resp = requests.post(f"{BASE_URL}/api/localtrack/metrics/query", json=query_payload).json()
    print(f"Retrieved {len(metrics_resp['series'])} metric series.")
    for s in metrics_resp['series']:
        print(f"Run {s['run_id']} | Key: {s['key']} | Points: {len(s['values'])} | Min: {min(s['values']):.4f}")"""),
    ]
    with open(nb_dir / "02_localtrack_experiment_analysis.ipynb", "w", encoding="utf-8") as f:
        nbf.write(nb2, f)

    # --- 3. LLM Evaluation Notebook ---
    nb3 = nbf.v4.new_notebook()
    nb3.cells = [
        nbf.v4.new_markdown_cell("""# 🧪 LLM Benchmark & Evaluation Suite with LocalTrack

Run automated multi-turn evaluations on candidate models and log evaluation tables, scoring distributions, and artifacts."""),
        nbf.v4.new_code_cell("""import json
import tempfile
from pathlib import Path
from backend.sdk.localtrack import LocalTrackClient

client = LocalTrackClient(base_url="http://127.0.0.1:8000")
run_id = client.init_run(
    project_name="eval-benchmarks",
    run_name="gsm8k-reasoning-eval",
    config={"eval_dataset": "gsm8k", "temperature": 0.0, "samples": 50},
    tags=["eval", "reasoning", "gsm8k"],
)
print(f"Started evaluation run: {run_id}")"""),
        nbf.v4.new_markdown_cell("""## Run Evaluation Loop & Log Scoring Progression"""),
        nbf.v4.new_code_cell("""benchmark_samples = [
    {"q": "Janet's ducks lay 16 eggs per day...", "correct": True},
    {"q": "A robe takes 2 bolts of blue fiber...", "correct": True},
    {"q": "Josh decides to try flipping a house...", "correct": False},
]

total_correct = 0
for idx, sample in enumerate(benchmark_samples, 1):
    if sample["correct"]:
        total_correct += 1
    accuracy = total_correct / idx
    client.log_metrics({
        "eval/running_accuracy": round(accuracy, 4),
        "eval/samples_processed": idx,
    }, step=idx)

# Upload benchmark summary artifact
with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
    json.dump({"dataset": "gsm8k", "total_samples": len(benchmark_samples), "final_accuracy": total_correct / len(benchmark_samples)}, f)
    tmp_name = f.name

client.log_artifact(tmp_name, "eval_summary.json")
Path(tmp_name).unlink(missing_ok=True)
client.finish_run(status="finished")
print("✅ Evaluation complete! Summary logged to LocalTrack.")"""),
    ]
    with open(nb_dir / "03_localtrack_llm_evaluations.ipynb", "w", encoding="utf-8") as f:
        nbf.write(nb3, f)

    print("Successfully generated template notebooks in notebooks/ directory.")

if __name__ == "__main__":
    create_template_notebooks()
