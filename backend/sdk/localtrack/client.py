"""Concrete LocalTrack client with asynchronous batching and offline resilience."""

from __future__ import annotations

import atexit
import logging
import os
import platform
import queue
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from backend.sdk.localtrack.base import BaseLocalTrackLogger

logger = logging.getLogger("localtrack")


class LocalTrackClient(BaseLocalTrackLogger):
    """High-performance LocalTrack logger client.

    Batches metric logs on a background worker thread so the training loop is
    never blocked by HTTP request latencies.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        batch_size: int = 50,
        flush_interval_sec: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec

        self.current_run_id: str | None = None
        self.current_project_id: str | None = None
        self.start_time: float = 0.0

        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10000)
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._client = httpx.Client(timeout=10.0)

        self._start_worker()
        atexit.register(self._cleanup)

    def _start_worker(self) -> None:
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        batch: list[dict[str, Any]] = []
        last_flush = time.time()

        while not self._stop_event.is_set():
            try:
                timeout = max(0.1, self.flush_interval_sec - (time.time() - last_flush))
                item = self._queue.get(timeout=timeout)
                if item is None:
                    break
                batch.append(item)
                if len(batch) >= self.batch_size:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.time()
            except queue.Empty:
                if batch:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.time()

        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        try:
            url = f"{self.base_url}/api/localtrack/metrics/ingest"
            resp = self._client.post(url, json={"logs": batch})
            if resp.status_code >= 400:
                logger.warning("LocalTrack: failed to ingest metrics: %s", resp.text)
        except Exception as exc:
            logger.debug("LocalTrack: error communicating with backend: %s", exc)

    def init_run(
        self,
        project_name: str,
        run_name: str | None = None,
        config: dict[str, Any] | None = None,
        system_info: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Create or register an experiment run with the LocalTrack server."""
        self.current_project_id = project_name
        self.start_time = time.time()

        # Capture default system info if not provided
        if system_info is None:
            system_info = {
                "os": platform.platform(),
                "python": platform.python_version(),
                "hostname": platform.node(),
                "pid": os.getpid(),
            }

        payload = {
            "project_id": project_name,
            "name": run_name or f"run-{int(time.time())}",
            "config": config or {},
            "system_info": system_info,
            "tags": tags or [],
        }

        try:
            url = f"{self.base_url}/api/localtrack/runs"
            resp = self._client.post(url, json=payload)
            if resp.status_code < 400:
                data = resp.json()
                self.current_run_id = data["id"]
                logger.info(
                    "LocalTrack: initialized run '%s' (ID: %s) in project '%s'",
                    data["name"],
                    data["id"],
                    project_name,
                )
                return self.current_run_id
            else:
                logger.error("LocalTrack: failed to create run: %s", resp.text)
        except Exception as exc:
            logger.warning("LocalTrack: backend unreachable (%s), running in offline mode", exc)

        # Fallback offline run ID
        self.current_run_id = f"offline-{int(time.time())}"
        return self.current_run_id

    def log_metrics(
        self,
        metrics: dict[str, float | int],
        step: int,
        epoch: float | None = None,
    ) -> None:
        """Enqueue metrics for background batch ingestion."""
        if not self.current_run_id or not metrics:
            return

        # Sanitize numeric metrics
        sanitized: dict[str, float] = {}
        for k, v in metrics.items():
            try:
                sanitized[str(k)] = float(v)
            except (ValueError, TypeError):
                continue

        if not sanitized:
            return

        item = {
            "run_id": self.current_run_id,
            "step": int(step),
            "epoch": float(epoch) if epoch is not None else None,
            "timestamp": time.time(),
            "metrics": sanitized,
        }

        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning("LocalTrack: metric queue is full, dropping item at step %d", step)

    def log_artifact(
        self,
        file_path: str,
        artifact_name: str | None = None,
    ) -> None:
        """Upload an artifact file (e.g. config.json, trainer_state.json) to the active run."""
        if not self.current_run_id:
            logger.warning("LocalTrack: cannot log artifact without active run")
            return

        p = Path(file_path)
        if not p.is_file():
            logger.warning("LocalTrack: artifact file '%s' not found", file_path)
            return

        name = artifact_name or p.name
        try:
            url = f"{self.base_url}/api/localtrack/runs/{self.current_run_id}/artifacts"
            with open(p, "rb") as f:
                files = {"file": (name, f)}
                resp = self._client.post(url, files=files)
                if resp.status_code < 400:
                    logger.info("LocalTrack: uploaded artifact '%s'", name)
                else:
                    logger.warning("LocalTrack: artifact upload failed: %s", resp.text)
        except Exception as exc:
            logger.warning("LocalTrack: error uploading artifact: %s", exc)

    def finish_run(self, status: str = "finished") -> None:
        """Flush pending metrics and update run status."""
        self._flush_queue()

        if self.current_run_id and not self.current_run_id.startswith("offline-"):
            duration = time.time() - self.start_time if self.start_time else 0.0
            try:
                url = f"{self.base_url}/api/localtrack/runs/{self.current_run_id}"
                self._client.patch(
                    url,
                    json={
                        "status": status,
                        "duration_seconds": duration,
                    },
                )
                logger.info(
                    "LocalTrack: run '%s' marked as %s (duration: %.1fs)",
                    self.current_run_id,
                    status,
                    duration,
                )
            except Exception as exc:
                logger.debug("LocalTrack: failed to update run finish status: %s", exc)

        self.current_run_id = None

    def _flush_queue(self) -> None:
        batch: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item is not None:
                    batch.append(item)
            except queue.Empty:
                break
        if batch:
            self._flush_batch(batch)

    def _cleanup(self) -> None:
        self._stop_event.set()
        self._flush_queue()
        try:
            self._client.close()
        except Exception:
            pass
