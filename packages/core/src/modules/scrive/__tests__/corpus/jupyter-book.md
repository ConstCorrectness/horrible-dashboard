---
title: Probabilistic programming, briefly
authors:
  - name: A. Writer
    affiliations: [Somewhere]
date: 2026-09-30
tags: [stats, myst]
kernelspec:
  name: python3
  display_name: Python 3
---

(sec:intro)=
# Introduction

We model $y_i \sim \mathcal{N}(\mu, \sigma^2)$ and estimate the posterior,
see {ref}`sec:model` and {cite}`gelman2013`.

```{math}
:label: eq:posterior
p(\theta \mid y) \propto p(y \mid \theta)\, p(\theta)
```

As {eq}`eq:posterior` shows, the prior matters.

:::{note} On notation
:class: dropdown
Bold symbols are vectors.
:::

```{code-cell} python
:tags: [hide-input]
import numpy as np
rng = np.random.default_rng(0)
y = rng.normal(1.0, 2.0, size=100)
y.mean()
```

+++ {"part": "model"}

(sec:model)=
## The model

```{figure} media/dag.png
:name: fig-dag
:width: 60%

A directed acyclic graph.
```

| parameter | prior        |
| --------- | ------------ |
| $\mu$     | Normal(0, 5) |
| $\sigma$  | HalfNormal(2)|

````{tab-set}
```{tab-item} Python
Use PyMC.
```
```{tab-item} R
Use brms.
```
````

```{dropdown} Derivation
$$
\int_0^1 x^2\,dx = \frac{1}{3}
$$
```

% A comment the reader never sees.

Term
: Its definition.

Footnotes work[^fn] and so do [reference links][docs].

[^fn]: Hoisted by the parser, kept by the writer.

[docs]: https://mystmd.org/guide

```{r3f} scenes/posterior.tsx
:height: 420
:params: {"samples": 500}
```

A live demo from the Hub:

:::{space} webml-community/smollm-webgpu
:height: 640
:host: webml-community-smollm-webgpu.static.hf.space
:::
