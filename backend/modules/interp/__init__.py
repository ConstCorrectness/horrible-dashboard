"""The interpretability library and worker.

Two halves of what the suite's later tools (features, steering, lenses, circuits)
stand on:

- **The library** (`catalog.py`, `artifacts.py`, `compat.py`): the SAEs,
  transcoders and lenses this node can fetch, the ones it has, and whether each fits
  the model in front of it — three answers, never two.
- **The worker** (`env.py`, `worker.py`, `worker_main.py`): a long-lived process in
  its own CUDA venv that holds one Hugging Face model, for everything that needs
  torch. The backend itself stays torch-free.

Plus a read-only, cached Neuronpedia client (`neuronpedia.py`) for explanations of
features. See docs/modules/interp-suite.mdx.
"""

from backend.modules.interp.routes import router

__all__ = ["router"]
