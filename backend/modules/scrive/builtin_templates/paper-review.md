---
template:
  name: Paper review
  description: Summarise and critique one paper for practitioners.
  kind: post
  inputs:
    paper:
      description: Title, authors and link (arXiv or DOI) of the paper
      required: true
    angle:
      description: What the reader cares about — applying it, reproducing it, or judging it
      default: whether it is worth applying
tags: [paper-review]
---

:::{pending}
Lead (no heading): the paper ({{paper}}) in one sentence, and the verdict in one more. The reader should know in two sentences whether to keep reading, judged by {{angle}}.
:::

## The claim

:::{pending}
What the paper says it achieves, quoted or paraphrased precisely, with the headline number and what it is measured against.
:::

## How it works

:::{pending}
The method at the level a practitioner needs: the key idea, a diagram or equation if it carries the idea, and what is genuinely new versus prior work.
:::

## The evidence

:::{pending}
Experiments and results: datasets, baselines, ablations. A small table of the numbers that matter. Note what was not tested.
:::

## Critique

:::{pending}
Strengths and weaknesses, each specific: missing baselines, cherry-picked settings, compute costs, reproducibility (code? data?). Separate what the paper shows from what it implies.
:::

## Should you use it?

:::{pending}
A concrete recommendation for the reader's situation, and what would change the verdict.
:::
