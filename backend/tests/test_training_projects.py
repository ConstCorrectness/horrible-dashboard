"""Project store: slugs, CRUD, and projectsRoot override via settings."""

import json
import os
from pathlib import Path

import pytest

from backend.modules.training import projects
from backend.modules.training.models import EnvironmentRefModel


@pytest.fixture(autouse=True)
def projects_root(tmp_path):
    """Point training.projectsRoot at a temp dir through the settings store
    (conftest already isolates HORRIBLE_DATA_DIR to tmp_path)."""
    root = tmp_path / "training-projects"
    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"training.projectsRoot": str(root)}))
    return root


def _ref() -> EnvironmentRefModel:
    return EnvironmentRefModel(
        provider="gymnasium", kind="env", id="CartPole-v1", title="CartPole-v1"
    )


def test_projects_root_honors_setting(projects_root: Path) -> None:
    assert projects.projects_root() == projects_root


def test_slugify() -> None:
    assert projects.slugify("Pokemon TCG Competition!") == "pokemon-tcg-competition"
    assert projects.slugify("  ---  ") == "project"


def test_create_list_get_delete(projects_root: Path) -> None:
    project = projects.create_project("My CartPole", [_ref()], "3.12")
    assert project.id == "my-cartpole"
    assert (projects_root / "my-cartpole" / "project.json").is_file()
    assert (projects_root / "my-cartpole" / "data").is_dir()
    assert (projects_root / "my-cartpole" / "media").is_dir()

    listed = projects.list_projects()
    assert [p.id for p in listed] == ["my-cartpole"]
    assert listed[0].refs[0].id == "CartPole-v1"

    fetched = projects.get_project("my-cartpole")
    assert fetched is not None and fetched.name == "My CartPole"

    assert projects.delete_project("my-cartpole") is True
    assert projects.get_project("my-cartpole") is None
    assert not (projects_root / "my-cartpole").exists()


def test_duplicate_names_get_unique_slugs() -> None:
    first = projects.create_project("Run", [_ref()], "3.12")
    second = projects.create_project("Run", [_ref()], "3.12")
    assert first.id == "run"
    assert second.id == "run-2"


def test_get_project_rejects_traversal(projects_root: Path) -> None:
    projects.create_project("Safe", [_ref()], "3.12")
    assert projects.get_project("../outside") is None
    assert projects.get_project("..") is None


def test_moved_tree_root_follows_disk(projects_root: Path) -> None:
    project = projects.create_project("Movable", [_ref()], "3.12")
    # Simulate the whole projects tree having been copied elsewhere: stored root
    # is stale, the on-disk directory wins.
    stored = json.loads((projects_root / project.id / "project.json").read_text())
    stored["root"] = "C:/somewhere/else"
    (projects_root / project.id / "project.json").write_text(json.dumps(stored))
    reread = projects.get_project(project.id)
    assert reread is not None
    assert Path(reread.root) == projects_root / project.id


def test_update_project_persists_flags() -> None:
    project = projects.create_project("Flags", [_ref()], "3.12")
    project.venv_ready = True
    project.data_ready = True
    projects.update_project(project)
    reread = projects.get_project(project.id)
    assert reread is not None
    assert reread.venv_ready is True and reread.data_ready is True


def _orphan_dir(root: Path, slug: str) -> Path:
    """A partial project dir: a built `.venv` but no `project.json` (the state that
    triggered `unknown project: <slug>`)."""
    d = root / slug
    (d / ".venv").mkdir(parents=True)
    return d


def test_orphan_dir_is_not_a_project(projects_root: Path) -> None:
    _orphan_dir(projects_root, "frozenlake-v1")
    assert projects.get_project("frozenlake-v1") is None
    assert projects.list_projects() == []


def test_recreate_adopts_orphan_slug(projects_root: Path) -> None:
    # A stray `.venv`-only dir must NOT push the new project to `-2`; creation
    # adopts the slug, writes project.json, and reuses the existing venv.
    orphan = _orphan_dir(projects_root, "frozenlake-v1")
    project = projects.create_project("FrozenLake-v1", [_ref()], "3.12")
    assert project.id == "frozenlake-v1"
    assert (orphan / "project.json").is_file()
    assert (orphan / ".venv").is_dir()  # adopted, not clobbered
    assert projects.get_project("frozenlake-v1") is not None


def test_delete_removes_orphan_dir(projects_root: Path) -> None:
    orphan = _orphan_dir(projects_root, "frozenlake-v1")
    # get_project returns None for it, but delete must still clean it up.
    assert projects.delete_project("frozenlake-v1") is True
    assert not orphan.exists()


def test_delete_rejects_non_slug(projects_root: Path) -> None:
    assert projects.delete_project("../outside") is False
    assert projects.delete_project("..") is False


def test_delete_missing_returns_false(projects_root: Path) -> None:
    assert projects.delete_project("nope") is False
