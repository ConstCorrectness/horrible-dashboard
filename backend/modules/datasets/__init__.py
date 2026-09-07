"""Datasets: the material a fine-tune is made of, as a first-class object.

The training module could always *name* a dataset — `Recipe.dataset` was a string —
and that was the whole of it. Meanwhile four different places in this codebase
already knew how to produce or inspect training material (the Hub peek layer in
`evals`, the Hub browser in `lab`, `evals.export`'s SFT jsonl, `trajectories`'
graded-run exporter) and **none of them was reachable from the recipe form**. The
gap that mattered most was not "we cannot list datasets"; it was that a recipe
pointed at a name nobody had looked at, in a shape nobody had checked, whose
examples nobody had counted tokens for — and the first sign of a mistake was a
wasted run.

So a dataset here is a *registered* thing: a source, a ref, a split, a **detected
format**, and a column map that adapts it to whatever the selected task needs.
Registering one is what lets a sweep's twelve runs provably eat the same data.

Four sources ship: `hub` (Hugging Face, peeked without downloading), `local`
(files in a project or the data dir), `exports` (what `evals` and `trajectories`
already write — this is the spoke that closes the flywheel), and `kaggle`.
Plugins add more through `host.add_dataset_source`.
"""

from backend.modules.datasets.agent_tools import register_agent_tools
from backend.modules.datasets.routes import router
from backend.modules.datasets.stream import subscribe_datasets_conn

__all__ = ["register_agent_tools", "router", "subscribe_datasets_conn"]
