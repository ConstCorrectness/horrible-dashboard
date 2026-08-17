---
name: wandb-eval-tables
description: Non-destructive conversion and inspection of Weights & Biases (W&B) Table artifacts into bounded, verified evaluation preview tables and summaries.
---

# Weights & Biases (W&B) Eval Tables Skill

Use this skill when inspecting, converting, or visualizing evaluation datasets, test predictions, and tabular artifacts from W&B experiments.

## Capabilities & Patterns

### 1. Logging W&B Evaluation Tables
```python
import wandb

run = wandb.init(project="eval-analysis")

# Construct table columns and rows
table = wandb.Table(columns=["id", "prompt", "prediction", "ground_truth", "score"])
for sample in test_samples:
    table.add_data(
        sample["id"],
        sample["prompt"],
        sample["prediction"],
        sample["ground_truth"],
        sample["score"],
    )

wandb.log({"eval_results_table": table})
wandb.finish()
```

### 2. Fetching and Analyzing Tables via W&B Public API
```python
import wandb

api = wandb.Api()
artifact = api.artifact("entity/project/run-id-eval_results_table:v0", type="run_table")
table = artifact.get("eval_results_table")

# Convert to Pandas DataFrame for analysis
df = table.get_dataframe()
print(df.describe())
print(df[df["score"] < 0.5].head(10))
```

### 3. Guidelines
- Use bounded sample extraction for large tables (>10,000 rows).
- Compute aggregated metrics (precision, recall, mean score) alongside the full raw table.
