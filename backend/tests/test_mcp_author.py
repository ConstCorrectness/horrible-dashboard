"""Scaffolding, editing and running an MCP server of one's own.

Three things are worth testing here and the rest is plumbing:

- **the scaffold's product is a server config**, because that is the design claim —
  an authored server is not a second kind of server, and if the config it writes isn't
  a config the existing session manager can start, the whole approach is wrong;
- **the path guard**, asserted on resolved paths rather than on strings, since the
  attacks that matter (`..`, an absolute path, a symlink) all produce something that
  looks relative;
- **the scaffolded Python template actually speaks MCP** — it is spawned with this
  interpreter and connected to for real, which is the only way to catch a template
  that is merely syntactically valid.

Provisioning itself is not tested end to end: `uv venv` + `uv pip install mcp` is a
network install taking tens of seconds, and what would be asserted — that uv works — is
not this module's claim. The command *construction* is asserted instead, since that is
the part we get wrong.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.modules.mcp import author
from backend.modules.mcp import config as cfg


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # `_projects` is process-global live state; a leaked entry from another test would
    # make `get_project` return a project whose directory no longer exists.
    author._projects.clear()
    return tmp_path


# --- scaffolding --------------------------------------------------------------


def test_scaffold_writes_a_runnable_project(data_dir: Path):
    project, err = author.create_project("mine", "python", "My Tools")
    assert err is None
    assert project is not None
    assert (project.root / "server.py").is_file()
    assert (project.root / "pyproject.toml").is_file()
    assert "My Tools" in (project.root / "server.py").read_text(encoding="utf-8")


def test_scaffold_registers_the_server_disabled(data_dir: Path):
    """Enabled at scaffold time, the server would try to spawn an interpreter that
    does not exist yet and report a spawn error instead of "not provisioned"."""
    author.create_project("mine", "python", "My Tools")
    config = cfg.get_server("mine")
    assert config is not None
    assert config["enabled"] is False
    assert config["transport"] == "stdio"
    assert config["origin"] == "authored"
    assert config["project"] == "mine"
    # The command is the project's own interpreter, not the backend's — a server that
    # imported whatever the backend has installed would work here and nowhere else.
    assert config["command"] == str(author.python_path(_project("mine")))
    assert config["args"] == ["server.py"]
    assert cfg.validate(config) is None


def test_scaffold_rejects_a_colliding_id(data_dir: Path):
    author.create_project("mine", "python")
    _, err = author.create_project("mine", "python")
    assert err is not None and "already exists" in err


def test_scaffold_rejects_an_id_that_cannot_be_a_tool_group(data_dir: Path):
    _, err = author.create_project("Not Legal!", "python")
    assert err is not None
    assert not (author.projects_dir() / "Not Legal!").exists()


def test_node_template_is_a_module_with_the_sdk_dependency(data_dir: Path):
    project, err = author.create_project("jsone", "node", "JS One")
    assert err is None and project is not None
    package = json.loads((project.root / "package.json").read_text(encoding="utf-8"))
    assert package["type"] == "module"
    assert "@modelcontextprotocol/sdk" in package["dependencies"]
    config = cfg.get_server("jsone")
    assert config is not None
    assert config["command"] == "node"
    assert config["args"] == ["server.mjs"]


def _project(project_id: str) -> author.Project:
    project = author.get_project(project_id)
    assert project is not None
    return project


# --- the file guard -----------------------------------------------------------


def test_listing_hides_the_dependency_tree(data_dir: Path):
    """`.venv`/`node_modules` would bury the three files the user wrote."""
    project, _ = author.create_project("mine", "python")
    assert project is not None
    (project.root / ".venv" / "Lib").mkdir(parents=True)
    (project.root / ".venv" / "Lib" / "thing.py").write_text("x = 1", encoding="utf-8")
    files = author.list_files(project)
    assert "server.py" in files
    assert not any(f.startswith(".venv") for f in files)
    # The project's own manifest isn't source either.
    assert not any(".horrible-mcp" in f for f in files)


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "sub/../../escape.py",
        ".venv/Lib/thing.py",
    ],
)
def test_writes_cannot_escape_the_project(data_dir: Path, path: str):
    project, _ = author.create_project("mine", "python")
    assert project is not None
    with pytest.raises(ValueError):
        author.resolve_in_project(project, path)


def test_an_absolute_path_is_rejected(data_dir: Path, tmp_path: Path):
    """`Path(root) / "/etc/passwd"` discards the root entirely — the resolved-path
    check catches it where a string check for `..` would not."""
    project, _ = author.create_project("mine", "python")
    assert project is not None
    outside = tmp_path / "elsewhere.py"
    with pytest.raises(ValueError):
        author.resolve_in_project(project, str(outside))


def test_binary_suffixes_are_not_editable(data_dir: Path):
    project, _ = author.create_project("mine", "python")
    assert project is not None
    with pytest.raises(ValueError):
        author.resolve_in_project(project, "weights.bin")


def test_write_then_read_round_trips(data_dir: Path):
    project, _ = author.create_project("mine", "python")
    assert project is not None
    author.write_file(project, "notes.md", "# hi\n")
    assert author.read_file(project, "notes.md") == "# hi\n"


# --- what warrants a restart --------------------------------------------------


def test_source_edits_restart_and_prose_does_not(data_dir: Path):
    """A README restart would spend a process spawn on every keystroke in the notes;
    a `server.py` edit that doesn't restart leaves the pane listing dead tools."""
    project, _ = author.create_project("mine", "python")
    assert project is not None
    assert author.touches_runtime(project, "server.py")
    assert author.touches_runtime(project, "package.json")
    assert not author.touches_runtime(project, "README.md")


# --- provisioning commands ----------------------------------------------------


def test_python_provisioning_targets_the_project_venv(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(author, "_uv", lambda: "/usr/bin/uv")
    project, _ = author.create_project("mine", "python")
    assert project is not None
    commands, err = author.provision_commands(project)
    assert err is None
    assert commands[0][:3] == ["/usr/bin/uv", "venv", ".venv"]
    # `--python <the project's interpreter>` is what stops the install landing in the
    # backend's own environment, which would "work" until the next machine.
    assert "--python" in commands[1]
    assert str(author.python_path(project)) in commands[1]


def test_project_paths_are_absolute_even_when_the_data_dir_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`HORRIBLE_DATA_DIR` defaults to the relative `.data`, and every path derived
    from a project is later used with `cwd` set to the *project root* — so a relative
    one silently resolves somewhere that doesn't exist. `uv` reports that as "no
    virtual environment found", naming the venv it created seconds earlier.

    Every other test here runs under an absolute `tmp_path`, which is exactly why this
    one sets a relative dir on purpose.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", ".data")
    author._projects.clear()

    assert author.projects_dir().is_absolute()
    project, err = author.create_project("mine", "python")
    assert err is None and project is not None
    assert author.python_path(project).is_absolute()
    config = author.server_config(project)
    assert Path(config["command"]).is_absolute()
    assert Path(config["cwd"]).is_absolute()


def test_the_installed_sdk_is_the_one_the_template_is_written_against(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """The two drifting apart is a project that provisions cleanly and then cannot
    start. It happened: an unpinned `uv pip install mcp` fetched the 2.x SDK, which
    renamed `FastMCP` to `MCPServer`, so the scaffolded `server.py` died on
    `ModuleNotFoundError` — an error about the template, surfacing as a connection
    failure with no obvious link to the install that caused it.
    """
    monkeypatch.setattr(author, "_uv", lambda: "/usr/bin/uv")
    project, _ = author.create_project("mine", "python")
    assert project is not None

    pyproject = (project.root / "pyproject.toml").read_text(encoding="utf-8")
    assert author.PY_SDK_SPEC in pyproject
    commands, _ = author.provision_commands(project)
    assert author.PY_SDK_SPEC in commands[1]
    # The template imports FastMCP, which only exists below 2.0.
    assert "from mcp.server.fastmcp import FastMCP" in (
        project.root / "server.py"
    ).read_text(encoding="utf-8")
    assert "<2" in author.PY_SDK_SPEC


def test_a_missing_toolchain_is_named_not_attempted(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(author, "_uv", lambda: None)
    project, _ = author.create_project("mine", "python")
    assert project is not None
    commands, err = author.provision_commands(project)
    assert commands == []
    assert err is not None and "uv" in err


# --- deletion -----------------------------------------------------------------


def test_removing_a_project_keeps_the_source_by_default(data_dir: Path):
    """This is code the user wrote, and there is no undo."""
    project, _ = author.create_project("mine", "python")
    assert project is not None
    assert author.delete_project("mine", delete_files=False)
    assert cfg.get_server("mine") is None
    assert (project.root / "server.py").is_file()


def test_deleting_files_is_possible_when_asked(data_dir: Path):
    project, _ = author.create_project("mine", "python")
    assert project is not None
    assert author.delete_project("mine", delete_files=True)
    assert not project.root.exists()


# --- the template is a real MCP server ----------------------------------------


def test_a_server_that_dies_on_startup_reports_why(data_dir: Path):
    """The edit loop's most common failure: you save a syntax error and the server
    doesn't come back. Without the stderr tail the pane says `ExceptionGroup:
    unhandled errors in a TaskGroup`, which names neither the file nor the problem —
    the traceback went to a `DEVNULL` the user cannot reach.
    """
    from backend.modules.mcp.client import McpSession

    project, _ = author.create_project("mine", "python")
    assert project is not None
    author.write_file(project, "server.py", "this is not python(\n")

    async def go() -> str:
        session = McpSession(
            {
                "id": "mine",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(project.root / "server.py")],
                "env": {},
                "cwd": str(project.root),
            }
        )
        try:
            await session.start()
            assert session.runtime.state == "error"
            return session.runtime.error or ""
        finally:
            await session.stop()

    error = asyncio.run(go())
    assert "SyntaxError" in error, error


def test_the_python_template_actually_speaks_mcp(data_dir: Path):
    """Spawn the scaffolded file with *this* interpreter and complete a handshake.

    Provisioning is skipped deliberately: the point is the template's correctness, and
    the backend env already has `mcp`. A template that imports fine but registers no
    tools, or writes to stdout and corrupts the pipe, fails here and nowhere else.
    """
    from backend.modules.mcp.client import McpSession

    project, _ = author.create_project("mine", "python", "My Tools")
    assert project is not None

    async def go() -> None:
        session = McpSession(
            {
                "id": "mine",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(project.root / "server.py")],
                "env": {},
                "cwd": str(project.root),
            }
        )
        try:
            await session.start()
            runtime = session.runtime
            assert runtime.state == "ready", runtime.error
            names = {t.name for t in runtime.tools}
            assert {"ping", "remember"} <= names
            # The template's own claim: `ping` is annotated read-only and `remember`
            # is not, which is what the permission mapping keys on.
            assert next(t for t in runtime.tools if t.name == "ping").read_only
            assert not next(t for t in runtime.tools if t.name == "remember").read_only
            # `instructions` is the group guide; a template shipping none would teach
            # the wrong habit at the one moment the user is copying from it.
            assert runtime.instructions
        finally:
            await session.stop()

    asyncio.run(go())
