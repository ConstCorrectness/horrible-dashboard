"""Running a page's `{code-cell}` blocks — on the same kernel engine as the Notebook
editor, through a **shadow notebook**.

`notebook_core.KernelSession` runs an `.ipynb` that is authoritative for its cells
and outputs. A Scrive page is MyST, and MyST files do not store outputs, so each
page gets a shadow notebook at `<site>/.scrive/cache/cells/<page path>.ipynb`
(gitignored with the rest of `.scrive/`). The editor mirrors the page's code cells
into it over the `scrive-kernel` channel and runs them there; the outputs the kernel
writes back *are* the output cache.

Cell ids are `c-<hash of the source>-<n>` (`n` counts identical sources). So an
output stays attached to its source across edits elsewhere in the page, and a cell
whose source changed is a new cell with no output — which is exactly what "stale"
means. Publishing (Phase 5) embeds these outputs; preflight flags code cells that
have none.

The Python is the Notebook editor's managed venv: a library installed for one is
there for the other. The kernel's working directory is the page's folder, as
Jupyter Book uses when it executes a page, so relative data paths agree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from backend.modules.notebook import env as notebook_env
from backend.modules.scrive import store
from backend.notebook_core import KernelSession, KernelSessionManager, SessionConfig
from backend.notebook_core import notebooks as nb_core

CHANNEL = "scrive-kernel"


def cell_id(source: str, occurrence: int) -> str:
    """The shadow cell id for a code cell (mirrored by `cells.ts` in the editor)."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"c-{digest}-{occurrence}"


def shadow_path(site_id: str, rel_path: str) -> Path:
    """Where a page's code-cell outputs live. Escape-guarded through the page."""
    page = store.resolve_page(site_id, rel_path)
    base = store.site_dir(site_id).resolve()
    rel = page.relative_to(base).as_posix()
    return base / ".scrive" / "cache" / "cells" / f"{rel}.ipynb"


def cached_cells(site_id: str, rel_path: str) -> list[dict[str, Any]]:
    """The page's cached code cells — id, source, outputs, execution count — or `[]`
    when nothing has run yet. Read without starting a kernel."""
    path = shadow_path(site_id, rel_path)
    if not path.is_file():
        return []
    model = nb_core.to_model(nb_core.load(path), rel_path)
    return [
        {
            "id": cell.id,
            "source": cell.source,
            "outputs": cell.outputs,
            "execution_count": cell.execution_count,
        }
        for cell in model.cells
        if cell.cell_type == "code"
    ]


class ScriveKernelManager(KernelSessionManager):
    channel = CHANNEL
    SessionCls = KernelSession

    def _session_key(self, data: dict[str, Any]) -> str:
        site = str(data.get("site") or "")
        rel = str(data.get("path") or "").replace("\\", "/")
        if not site or not rel:
            raise ValueError("scrive kernel open requires a site and a path")
        page = store.resolve_page(site, rel)
        if page.suffix.lower() != ".md":
            raise ValueError(f"only MyST pages run code cells: {rel}")
        return f"scrive:{site}/{rel}"

    def _build_config(self, data: dict[str, Any], key: str) -> SessionConfig:
        site = str(data.get("site"))
        rel = str(data.get("path")).replace("\\", "/")
        page = store.resolve_page(site, rel)
        if not page.is_file():
            raise ValueError(f"page not found: {rel}")
        shadow = shadow_path(site, rel)
        if not shadow.is_file():
            nb_core.new_notebook(
                shadow,
                [],
                metadata={
                    "scrive": {"site": site, "page": rel},
                    "kernelspec": {
                        "name": "python3",
                        "display_name": "Python 3",
                        "language": "python",
                    },
                },
            )
        python_executable = notebook_env.ensure_python()
        notebook_env.start_library_install()
        return SessionConfig(
            key=key,
            python_executable=python_executable,
            cwd=str(page.parent),
            notebook_abs_path=shadow,
            rel_path=rel,
            channel=CHANNEL,
            display_name="scrive",
            # A page reads top to bottom, as Jupyter Book executes it.
            default_mode="classic",
        )

    def _opened_extra(
        self, session: KernelSession, data: dict[str, Any]
    ) -> dict[str, Any]:
        return {"site": str(data.get("site")), "path": session.rel_path}


scrive_kernels = ScriveKernelManager()


async def handle_scrive_kernel_message(conn: Any, msg: dict[str, Any]) -> None:
    """`scrive-kernel` channel entry point."""
    await scrive_kernels.handle(conn, msg)
