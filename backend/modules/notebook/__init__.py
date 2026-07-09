"""Notebook module: a domain-neutral reactive `.ipynb` notebook.

JupyterLab-style cells plus a marimo-style reactive dataflow mode, running on a
kernel spawned from a managed venv (ipykernel + ipywidgets). Built on the shared
`backend/notebook_core/` engine. See docs/modules/notebook.mdx.
"""

from backend.modules.notebook.manager import handle_notebook_message, notebook_manager
from backend.modules.notebook.routes import router

__all__ = ["handle_notebook_message", "notebook_manager", "router"]
