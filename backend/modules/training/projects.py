"""Training-project store: one directory per project under the projects root.

The directory *is* the record — `project.json` inside it holds the ProjectModel
dump, so listing projects is a scan for that file (mirrors the database module's
file-backed connection store; no DB involved). Layout per project:

    main.ipynb        # scaffolded notebook (nbformat v4)
    project.json      # ProjectModel dump
    data/             # provider-fetched datasets
    media/            # manim renders, saved frames
    .venv/            # uv-managed project venv
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path

from backend.atomic_write import read_text_or_none, write_text_atomic
from backend.modules.settings.routes import get_value
from backend.modules.training.models import EnvironmentRefModel, ProjectModel

DEFAULT_NOTEBOOK = "main.ipynb"


def projects_root() -> Path:
    raw = str(get_value("training.projectsRoot", "~/horrible/training"))
    return Path(raw).expanduser()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def _unique_slug(name: str) -> str:
    """A slug not already used by a **valid** project. A directory that lacks a
    readable `project.json` (an orphan — e.g. a stray `.venv` from an aborted
    create) is not a project, so its slug is reusable: `create_project` writes
    `project.json` into it and adopts any existing `.venv` instead of stranding it
    behind a `-2` suffix."""
    base = slugify(name)
    root = projects_root()
    slug = base
    n = 2
    while _read(root / slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _project_file(root: Path) -> Path:
    return root / "project.json"


def _write(project: ProjectModel) -> None:
    """Persist the record atomically.

    Background workers call this (`_mark(project, venv_ready=True)`) while requests
    are reading the same file. The previous `write_text` truncated first, so a read
    landing in that window got an empty file, `_read` swallowed the `ValueError`,
    and the route answered **404 for a project that exists** — a self-healing bug
    that only appeared under load, surfacing as an unrelated-looking test failure.
    """
    write_text_atomic(
        _project_file(Path(project.root)),
        project.model_dump_json(indent=2),
        suffix=".project.tmp",
    )


def _read(directory: Path) -> ProjectModel | None:
    # Not `is_file()` + `read_text()`: on Windows an open lands mid-replace as a
    # PermissionError, which is "come back in a moment", not "no such project".
    raw = read_text_or_none(_project_file(directory))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    try:
        project = ProjectModel.model_validate(data)
    except ValueError:
        return None
    # The tree may have been moved/copied; the directory on disk wins.
    project.root = str(directory)
    return project


def list_projects() -> list[ProjectModel]:
    root = projects_root()
    if not root.is_dir():
        return []
    found = [p for d in sorted(root.iterdir()) if d.is_dir() and (p := _read(d))]
    return found


def get_project(project_id: str) -> ProjectModel | None:
    root = projects_root()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_id):
        return None  # ids are slugs; anything else could escape the root
    return _read(root / project_id)


def create_project(
    name: str, refs: list[EnvironmentRefModel], python: str, owner: str = ""
) -> ProjectModel:
    """A new project directory. `owner` names the module that owns it as working
    storage (see `ProjectModel.owner`); leave it empty for a user's own project.

    Note this creates the *directory*, not a notebook — `main.ipynb` is scaffolded by
    the create **route**, from the environment provider's cells. A caller that comes
    straight here (as `evals` does) therefore gets a project with no notebook at all,
    which is why owned projects do not offer to open one.
    """
    slug = _unique_slug(name)
    directory = projects_root() / slug
    (directory / "data").mkdir(parents=True, exist_ok=True)
    (directory / "media").mkdir(parents=True, exist_ok=True)
    project = ProjectModel(
        id=slug,
        name=name,
        root=str(directory),
        refs=refs,
        python=python,
        owner=owner,
        created_at=_dt.datetime.now(_dt.UTC).isoformat(),
    )
    _write(project)
    return project


def update_project(project: ProjectModel) -> ProjectModel:
    _write(project)
    return project


def delete_project(project_id: str) -> bool:
    """Remove a project directory by slug. Deletes **partial/orphan** dirs too (a
    dir with no readable `project.json`), so a corrupt project can be cleaned up —
    `get_project` would return `None` for those, which previously left them
    undeletable. The slug regex guards against `..`/absolute escapes out of the
    projects root."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_id):
        return False
    directory = projects_root() / project_id
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True
