"""Manim renders in the project venv.

`python -m manim render` runs against a scene file under `media/scenes/`
(written from the request, or an existing project file). Popen-on-thread like
every training subprocess; progress lines stream as `manim_status`, and on
success the located mp4 is announced as `manim_done` with a URL served by the
media route (`GET /api/training/projects/{id}/media/...`). Manim is installed
into the venv on first use (announced via `env_progress`).
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from backend.modules.training import envs
from backend.modules.training.models import ManimRequest, ProjectModel
from backend.modules.training.providers.base import ProviderError
from backend.modules.training.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)

QUALITY_FLAGS = {"l": "-ql", "m": "-qm", "h": "-qh"}


class ManimRunner:
    def render(self, project: ProjectModel, req: ManimRequest) -> None:
        """Kick off a render on a daemon thread (returns immediately)."""
        threading.Thread(
            target=self._render,
            args=(project, req),
            daemon=True,
            name=f"manim-{project.id}",
        ).start()

    # --- worker ---------------------------------------------------------------

    def _status(self, project: ProjectModel, line: str) -> None:
        broadcast_threadsafe("manim_status", {"projectId": project.id, "line": line})

    def _render(self, project: ProjectModel, req: ManimRequest) -> None:
        try:
            scene_file = self._scene_file(project, req)
            self._ensure_manim(project)
            self._run_manim(project, req, scene_file)
        except (ProviderError, ValueError) as exc:
            self._status(project, f"manim failed: {exc}")
        except Exception:
            logger.exception("manim render failed (%s)", project.id)
            self._status(project, "manim failed — see backend log")

    def _scene_file(self, project: ProjectModel, req: ManimRequest) -> Path:
        root = Path(project.root).resolve()
        if req.source is not None:
            scene_dir = root / "media" / "scenes"
            scene_dir.mkdir(parents=True, exist_ok=True)
            path = scene_dir / f"{req.scene}.py"
            path.write_text(req.source, encoding="utf-8")
            return path
        if not req.file:
            raise ValueError("manim request needs `source` or `file`")
        path = (root / req.file).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"bad scene file: {req.file}")
        return path

    def _ensure_manim(self, project: ProjectModel) -> None:
        probe = subprocess.run(
            [str(envs.python_path(project)), "-c", "import manim"],
            capture_output=True,
            timeout=120,
        )
        if probe.returncode != 0:
            self._status(project, "installing manim into the project venv…")
            envs.install(
                project,
                ["manim"],
                lambda line: broadcast_threadsafe(
                    "env_progress", {"projectId": project.id, "line": line}
                ),
            )

    def _run_manim(
        self, project: ProjectModel, req: ManimRequest, scene_file: Path
    ) -> None:
        root = Path(project.root)
        media_dir = root / "media"
        quality = QUALITY_FLAGS.get(req.quality, "-qm")
        cmd = [
            str(envs.python_path(project)),
            "-m",
            "manim",
            "render",
            quality,
            "--media_dir",
            str(media_dir),
            str(scene_file),
            req.scene,
        ]
        self._status(project, "$ " + " ".join(cmd[2:]))
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            stripped = line.rstrip()
            if stripped:
                self._status(project, stripped)
        code = proc.wait()
        if code != 0:
            self._status(project, f"manim exited with {code}")
            return
        video = self._find_output(media_dir, req.scene)
        if video is None:
            self._status(project, "render finished but no video found under media/")
            return
        rel = video.relative_to(media_dir).as_posix()
        broadcast_threadsafe(
            "manim_done",
            {
                "projectId": project.id,
                "scene": req.scene,
                "url": f"/api/training/projects/{project.id}/media/{rel}",
            },
        )

    @staticmethod
    def _find_output(media_dir: Path, scene: str) -> Path | None:
        candidates = sorted(
            media_dir.glob(f"videos/**/{scene}.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None


manim_runner = ManimRunner()
