import asyncio
import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from typing import Any, Awaitable
from backend import paths

logger = logging.getLogger(__name__)


def _get_db_conn() -> sqlite3.Connection:
    data_dir = paths.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "app.db"))
    conn.row_factory = sqlite3.Row
    return conn


def init_queue_db() -> None:
    with _get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS async_tasks (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_status ON async_tasks(status)"
        )


def enqueue_task(task_type: str, payload: dict[str, Any]) -> str:
    """Queue a background task."""
    init_queue_db()
    task_id = uuid.uuid4().hex
    with _get_db_conn() as conn:
        conn.execute(
            "INSERT INTO async_tasks (id, task_type, payload, status) VALUES (?, ?, ?, 'pending')",
            (task_id, task_type, json.dumps(payload)),
        )
    return task_id


def get_task_status(task_id: str) -> dict[str, Any] | None:
    """Retrieve task status."""
    init_queue_db()
    with _get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM async_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "task_type": r["task_type"],
        "payload": json.loads(r["payload"]),
        "status": r["status"],
        "error": r["error"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


class TaskQueue:
    def __init__(self):
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self._running = False
        self._worker_task: asyncio.Task | None = None

    def register_handler(
        self, task_type: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ):
        self.handlers[task_type] = handler

    def start(self):
        if self._running:
            return
        init_queue_db()
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    def stop(self):
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()

    async def _worker_loop(self):
        while self._running:
            task = self._claim_next_task()
            if not task:
                await asyncio.sleep(2.0)
                continue

            task_id = task["id"]
            task_type = task["task_type"]
            payload = json.loads(task["payload"])

            handler = self.handlers.get(task_type)
            if not handler:
                self._update_task_status(
                    task_id, "failed", f"No handler registered for {task_type}"
                )
                continue

            try:
                await handler(payload)
                self._update_task_status(task_id, "completed")
            except Exception as e:
                logger.exception(f"Task {task_id} failed")
                self._update_task_status(task_id, "failed", str(e))

    def _claim_next_task(self) -> sqlite3.Row | None:
        """Atomically claim the next pending task."""
        with _get_db_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT * FROM async_tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not task:
                return None
            conn.execute(
                "UPDATE async_tasks SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (task["id"],),
            )
            return task

    def _update_task_status(self, task_id: str, status: str, error: str | None = None):
        with _get_db_conn() as conn:
            conn.execute(
                "UPDATE async_tasks SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error, task_id),
            )


# Global queue instance
queue = TaskQueue()
