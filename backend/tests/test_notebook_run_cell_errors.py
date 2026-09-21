"""Why `run_cell` refused.

Two different failures used to share one message — "no code cell <id>" — and the
common one was the second: pressing Shift+Enter in a markdown cell. The training
notebook pane bound that key to "run this cell" and never rendered markdown at
all, so a prose cell answered with a sentence implying the notebook had lost the
cell you were looking at. They are different problems with different fixes, so
they say different things.
"""

import asyncio
from typing import Any

from backend.notebook_core.manager import KernelSessionManager


class _Conn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _Session:
    """The slice of a live session `run_cell` touches."""

    key = "proj:main.ipynb"
    mode = "classic"

    def __init__(self, cells: dict[str, str]) -> None:
        self._cells = cells

    def cell_type(self, cell_id: str) -> str | None:
        return self._cells.get(cell_id)

    def enqueue(self, cell_id: str) -> bool:
        return self._cells.get(cell_id) == "code"


def _run_cell(session: _Session, cell_id: str) -> str:
    conn = _Conn()
    manager = KernelSessionManager()
    asyncio.run(
        manager._handle_session_event(conn, session, "run_cell", {"cellId": cell_id})
    )
    assert conn.sent, "a refusal must say something"
    return str(conn.sent[-1]["data"]["message"])


def test_a_markdown_cell_says_it_is_markdown() -> None:
    session = _Session({"9f5fc648": "markdown"})
    message = _run_cell(session, "9f5fc648")
    assert "markdown" in message
    assert "no cell" not in message


def test_a_missing_cell_says_it_is_missing() -> None:
    session = _Session({"a": "code"})
    message = _run_cell(session, "gone")
    assert message == "no cell gone in this notebook"


def test_a_code_cell_is_not_refused() -> None:
    conn = _Conn()
    asyncio.run(
        KernelSessionManager()._handle_session_event(
            conn, _Session({"a": "code"}), "run_cell", {"cellId": "a"}
        )
    )
    assert conn.sent == []
