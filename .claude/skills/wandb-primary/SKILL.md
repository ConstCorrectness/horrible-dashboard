---
name: wandb-primary
description: Comprehensive Weights & Biases (W&B) and Weave skill for AI coding agents. Covers experiment tracking, logging training runs, metric querying, hyperparameter sweeps, artifact versioning, LLM observability with Weave traces, and model evaluation.
---

# Weights & Biases (W&B) Primary Skill

Use this skill when performing machine learning experiment tracking, hyperparameter optimization, model evaluations, and LLM observability using Weights & Biases and Weave.

## Core Capabilities

### 1. W&B Models & Experiment Tracking SDK
- **Initialize Runs**:
  ```python
  import wandb

  run = wandb.init(
      project="my-project",
      name="run-experiment-1",
      config={
          "learning_rate": 2e-4,
          "batch_size": 32,
          "epochs": 10,
          "model": "llama-3-8b",
      },
      tags=["baseline", "lora"],
  )
  ```
- **Log Metrics**:
  ```python
  for step, (loss, acc) in enumerate(train_data):
      wandb.log({"train/loss": loss, "train/acc": acc}, step=step)
  ```
- **Artifact Versioning & Logging**:
  ```python
  artifact = wandb.Artifact(name="trained-model", type="model")
  artifact.add_file("model_weights.pt")
  artifact.add_dir("checkpoints/")
  wandb.log_artifact(artifact)
  ```
- **Hugging Face Trainer Callback**:
  Use `report_to="wandb"` in `TrainingArguments` or add custom callbacks.

### 2. Weave LLM Observability & Tracing SDK
- **Initialize Weave**:
  ```python
  import weave

  weave.init("my-weave-project")
  ```
- **Trace Functions & LLM Calls**:
  ```python
  @weave.op()
  def generate_response(prompt: str) -> str:
      response = client.chat.completions.create(
          model="gpt-4o",
          messages=[{"role": "user", "content": prompt}],
      )
      return response.choices[0].message.content
  ```
- **Model Evaluation with Weave**:
  ```python
  dataset = [{"input": "Hello", "target": "Hi there!"}]

  @weave.op()
  def exact_match(target: str, output: str) -> dict:
      return {"match": target.strip().lower() == output.strip().lower()}

  evaluation = weave.Evaluation(dataset=dataset, scorers=[exact_match])
  # asyncio.run(evaluation.evaluate(my_model))
  ```

## Best Practices & Guidelines

1. **Authentication**: Set `WANDB_API_KEY` in environment variables or configure via `wandb.login()`.
2. **Project Targeting**: Always specify a clear project name (`wandb.init(project=...)`).
3. **Finish Runs**: Always call `wandb.finish()` or use `with wandb.init(...)` contexts to ensure all metric buffers and artifacts upload completely.
4. **Summary Metrics**: Set summary metrics (e.g. `wandb.summary["best_val_loss"] = min_loss`) for sorting and sweep comparisons.
