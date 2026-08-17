---
name: wandb-autoresearch
description: Automated bounded machine learning research, hyperparameter optimization, and serial trial analysis using Weights & Biases Launch and sweep controllers.
---

# Weights & Biases (W&B) AutoResearch Skill

Use this skill when orchestrating autonomous training trials, parameter searches, and automated experiment comparison loops.

## Workflow

1. **Readiness Check**: Validate that compute environment, GPU drivers, dataset paths, and `WANDB_API_KEY` are ready.
2. **Sweep Configuration**: Define the search space and optimization metric:
   ```yaml
   method: bayes
   metric:
     name: eval/loss
     goal: minimize
   parameters:
     learning_rate:
       min: 0.00001
       max: 0.001
     batch_size:
       values: [16, 32, 64]
     weight_decay:
       values: [0.0, 0.01, 0.1]
   ```
3. **Execution**: Run agent trials with bounds (max duration or step count per trial).
4. **Comparison**: Query the best performing trial configs and synthesize findings.
