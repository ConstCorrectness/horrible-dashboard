"""Authoring MCP servers: scaffold, provision, edit, run.

The whole design decision here is that **an authored server is not a second kind of
server**. Scaffolding writes a project directory and then registers an ordinary stdio
entry in `mcp-servers.json` pointing at it, so the session supervisor, the transcript
tee, the bridge, the cost view and the conformance suite all work on it unchanged. The
only thing this module adds to a server's config is provenance (`origin="authored"`)
and a back-pointer to the project (`project=<id>`).

**Provenance, not a gate.** The plan called for gating spawned servers the way the
games module gates code execution. On inspection that would be theatre: `POST
/api/mcp/servers` has always accepted an arbitrary `command` + `args` for a stdio
server and spawned it, so authoring adds no capability that isn't already reachable —
it adds *files we wrote*. A gate on the authoring routes alone would suggest a boundary
that doesn't exist. What is genuinely useful, and is implemented instead, is labelling:
`origin` distinguishes code the user wrote here (`authored`) from third-party code
installed from the registry (`registry`) from a command they typed themselves
(`manual`), and the pane says which before it runs anything.

**Provisioning spawns blocking `subprocess.Popen` on a daemon thread**, not
`asyncio.create_subprocess_exec` — under `uvicorn --reload` on Windows the event loop
is a `SelectorEventLoop` where asyncio subprocess spawn fails outright. This is the
same pattern `backend/modules/training/envs.py` and the LSP manager use, and for the
same reason.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from backend.modules.mcp import config as cfg
from backend import paths

logger = logging.getLogger(__name__)

Template = Literal["python", "node"]
ProvisionState = Literal["new", "provisioning", "ready", "error"]

# Lines of provisioning output kept per project. `uv pip install` is chatty and only
# the tail matters when it fails.
MAX_LOG_LINES = 400

# A project id doubles as the MCP server id, so it obeys exactly the same charset —
# validated by `cfg.validate_id` rather than a second regex that could drift.

# Files a template writes. Editing is confined to the project directory (see
# `resolve_in_project`); this is only what the scaffold creates.
_PY_SERVER = '''"""An MCP server, scaffolded by horrible-dashboard.

Everything below is yours to change. Two things are worth keeping:

- `instructions=` becomes the agent's **group guide** — it is injected only when this
  server's tool group is loaded, so it costs nothing on turns that never touch it.
  It is the cheapest documentation surface MCP gives you.
- `annotations={"readOnlyHint": True}` is what tells the dashboard a tool is safe to
  run without a permission prompt. Leave it off and the tool is gated, which is the
  right default for anything that writes.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "{title}",
    instructions="{title} — describe here how an agent should drive this server.",
)


@mcp.tool(annotations={"readOnlyHint": True})
def ping(message: str = "hello") -> str:
    """Echo a message back. Read-only, so the agent may call it unprompted."""
    return f"pong: {message}"


@mcp.tool()
def remember(key: str, value: str) -> str:
    """Store a value. Has side effects, so the agent's permission gate applies."""
    _MEMORY[key] = value
    return f"stored {key}"


_MEMORY: dict[str, str] = {}


@mcp.resource("memory://{key}")
def read_memory(key: str) -> str:
    """Read back something `remember` stored."""
    return _MEMORY.get(key, "")


if __name__ == "__main__":
    # stdio: the dashboard spawns this file and speaks JSON-RPC over the pipe. Never
    # print to stdout from your own code — stdout *is* the protocol channel.
    mcp.run(transport="stdio")
'''

# The SDK version the Python template is *written against*, used both in the generated
# `pyproject.toml` and in the install command — one constant, because the two drifting
# apart is a project that provisions cleanly and then cannot start.
#
# It is pinned below 2.0 deliberately. The 2.x SDK renamed `FastMCP` to `MCPServer` and
# moved it to `mcp.server.mcpserver`, so an unpinned `uv pip install mcp` produced
# exactly that failure: Provision succeeds, and the server dies on
# `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` — an error about the
# template, surfacing as a connection failure. Pinning to the same major the app itself
# runs also means what you test here is what the client was built against. It is one
# line in a file the user owns, and the template says how to move it.
PY_SDK_SPEC = "mcp>=1.28,<2"

_PY_PYPROJECT = """[project]
name = "{id}"
version = "0.1.0"
description = "{title}"
requires-python = ">=3.10"
# Pinned below 2.0: the 2.x SDK renames FastMCP to MCPServer
# (`from mcp.server.mcpserver import MCPServer`). Bump this and that import together.
dependencies = ["{sdk}"]
"""

_NODE_SERVER = """/**
 * An MCP server, scaffolded by horrible-dashboard.
 *
 * Uses the official TypeScript SDK from plain ESM JavaScript — deliberately no build
 * step, because the edit loop here is "save, restart", and a `tsc` pass between them
 * would mean a stale binary running against fresh source.
 *
 * `readOnlyHint` is load-bearing: without it a tool is gated behind the agent's
 * permission prompt, which is the right default for anything that writes.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const server = new McpServer(
  { name: '{title}', version: '0.1.0' },
  {
    instructions:
      '{title} — describe here how an agent should drive this server.',
  },
);

server.registerTool(
  'ping',
  {
    description: 'Echo a message back. Read-only, so the agent may call it unprompted.',
    inputSchema: { message: z.string().default('hello') },
    annotations: { readOnlyHint: true },
  },
  async ({ message }) => ({ content: [{ type: 'text', text: `pong: ${message}` }] }),
);

const memory = new Map();

server.registerTool(
  'remember',
  {
    description: 'Store a value. Has side effects, so the permission gate applies.',
    inputSchema: { key: z.string(), value: z.string() },
  },
  async ({ key, value }) => {
    memory.set(key, value);
    return { content: [{ type: 'text', text: `stored ${key}` }] };
  },
);

// stdout is the protocol channel — log to stderr, never console.log.
await server.connect(new StdioServerTransport());
"""

_NODE_PACKAGE = """{{
  "name": "{id}",
  "version": "0.1.0",
  "description": "{title}",
  "type": "module",
  "private": true,
  "dependencies": {{
    "@modelcontextprotocol/sdk": "^1.12.0",
    "zod": "^3.23.0"
  }}
}}
"""

_README = """# {title}

An MCP server scaffolded by horrible-dashboard.

- **Entry point:** `{entry}`
- **Run:** the dashboard spawns it over stdio; you don't start it yourself.
- **Edit:** save a file in the Author pane and the running server restarts, so the
  agent sees the new tool list without a reconnect.
- **Check it:** run the conformance suite from the server's inspector. It never calls
  your tools (a suite with side effects is worse than no suite) — it checks the
  handshake, capability negotiation, schema shape, error behaviour and annotations.

Tools become `mcp-{id}.<tool>` in the agent's catalog and load on demand as the
`mcp-{id}` tool group.
"""


@dataclass
class Project:
    """One authored server on disk, plus whatever provisioning has told us."""

    id: str
    title: str = ""
    template: Template = "python"
    state: ProvisionState = "new"
    error: str = ""
    log: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))

    @property
    def root(self) -> Path:
        return projects_dir() / self.id

    @property
    def entry(self) -> str:
        return "server.py" if self.template == "python" else "server.mjs"

    @property
    def registered(self) -> bool:
        """Whether an `mcp-servers.json` entry currently points at this project.

        The list is derived from directories on disk, not from the server config, so
        "Remove" — which unregisters and deliberately keeps the source — leaves a
        directory behind. Hiding it would be the worse failure: scaffolding the same id
        again fails on "a project directory already exists" while the pane shows
        nothing that could explain why. So an unregistered project stays visible and
        says so, and can be added back.
        """
        return cfg.get_server(self.id) is not None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title or self.id,
            "template": self.template,
            "state": self.state,
            "error": self.error,
            "root": str(self.root),
            "entry": self.entry,
            "registered": self.registered,
            "files": [f for f in list_files(self)],
            "log": list(self.log),
        }


def projects_dir() -> Path:
    """Absolute, always.

    `HORRIBLE_DATA_DIR` defaults to the relative `.data`, and every path derived from a
    project is used somewhere with a *different* working directory than the backend's:
    `uv pip install --python <path>` and the spawned server both run with `cwd` set to
    the project root, where a relative `.data/mcp-projects/x/.venv/...` resolves to a
    path that does not exist. `uv` reports that as "no virtual environment found" —
    naming the venv it had just created moments earlier.
    """
    root = paths.data_dir().resolve()
    return root / "mcp-projects"


def _manifest_path(root: Path) -> Path:
    """Where a project's own metadata lives.

    Kept inside the project rather than in a central index so a project directory is
    self-describing: copy it to another machine and it still knows what it is.
    """
    return root / ".horrible-mcp.json"


# Live provisioning state, keyed by project id. The manifest on disk is the durable
# half; this holds the log ring and the in-flight `provisioning` state, neither of
# which should survive a restart claiming to be true.
_projects: dict[str, Project] = {}
_lock = threading.Lock()


def _load(project_id: str) -> Project | None:
    root = projects_dir() / project_id
    if not root.is_dir():
        return None
    with _lock:
        existing = _projects.get(project_id)
    if existing is not None:
        return existing
    data: dict[str, Any] = {}
    try:
        data = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    template: Template = "node" if data.get("template") == "node" else "python"
    project = Project(
        id=project_id,
        title=str(data.get("title") or project_id),
        template=template,
    )
    # A project loaded from disk is `ready` if its runtime is actually there. Trusting
    # a `state: ready` written into the manifest would survive someone deleting
    # `.venv`, and the failure would then surface as an unreadable spawn error.
    project.state = "ready" if _runtime_present(project) else "new"
    with _lock:
        return _projects.setdefault(project_id, project)


def _runtime_present(project: Project) -> bool:
    if project.template == "python":
        return python_path(project).is_file()
    return (project.root / "node_modules").is_dir()


def python_path(project: Project) -> Path:
    venv = project.root / ".venv"
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def list_projects() -> list[Project]:
    root = projects_dir()
    if not root.is_dir():
        return []
    out: list[Project] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (project := _load(child.name)) is not None:
            out.append(project)
    return out


def get_project(project_id: str) -> Project | None:
    return _load(project_id)


# --- files -------------------------------------------------------------------

# Directories never listed or offered for editing. `.venv`/`node_modules` would bury
# the three files the user actually wrote under thousands of dependency files.
_SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", ".pytest_cache"}

# Editable extensions. A binary written through a JSON string field is corrupt by the
# time it lands, so the write path refuses rather than producing a broken file.
_TEXT_SUFFIXES = {".py", ".mjs", ".js", ".ts", ".json", ".toml", ".md", ".txt", ".cfg"}


def list_files(project: Project) -> list[str]:
    """Every editable file in the project, as project-relative posix paths."""
    root = project.root
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if rel.name == _manifest_path(root).name:
            continue
        if path.suffix.lower() in _TEXT_SUFFIXES:
            out.append(rel.as_posix())
    return out


def resolve_in_project(project: Project, rel: str) -> Path:
    """A project-relative path resolved to an absolute one, or `ValueError`.

    The check is on the **resolved** path, not on the string: `..%2F..` and a symlink
    pointing out of the tree both produce a path that looks relative and isn't, and
    string-matching for `..` catches only the first of them.
    """
    root = project.root.resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes the project directory")
    if candidate.suffix.lower() not in _TEXT_SUFFIXES:
        raise ValueError(f"{candidate.suffix or 'that file type'} is not editable here")
    if any(part in _SKIP_DIRS for part in candidate.relative_to(root).parts):
        raise ValueError("that directory is managed by the package installer")
    return candidate


def read_file(project: Project, rel: str) -> str:
    return resolve_in_project(project, rel).read_text(encoding="utf-8")


def write_file(project: Project, rel: str, text: str) -> None:
    path = resolve_in_project(project, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- scaffolding --------------------------------------------------------------


def server_config(project: Project) -> dict[str, Any]:
    """The `mcp-servers.json` entry that runs this project.

    Note the command is the project's **own** interpreter, not the backend's. A server
    that imports a package the backend happens to have installed would work here and
    fail on any other machine, and the failure would look like a protocol bug.
    """
    if project.template == "python":
        command = str(python_path(project))
        args = [project.entry]
    else:
        command = "node"
        args = [project.entry]
    return {
        "id": project.id,
        "name": project.title or project.id,
        "transport": "stdio",
        "command": command,
        "args": args,
        "cwd": str(project.root),
        "env": {},
        "enabled": True,
        "origin": "authored",
        "project": project.id,
    }


def create_project(
    project_id: str, template: Template, title: str = ""
) -> tuple[Project | None, str | None]:
    """Scaffold a project and register its server config. Returns `(project, error)`."""
    if err := cfg.validate_id(project_id):
        return None, err
    if template not in ("python", "node"):
        return None, "Template must be 'python' or 'node'."
    root = projects_dir() / project_id
    if root.exists():
        return None, f"A project directory named '{project_id}' already exists."
    if cfg.get_server(project_id) is not None:
        return None, f"An MCP server with id '{project_id}' is already configured."

    title = (title or project_id).strip()
    root.mkdir(parents=True)
    project = Project(id=project_id, title=title, template=template)

    if template == "python":
        # Substitution, not `str.format`: these templates are mostly literal braces —
        # dicts, f-strings, a `memory://{key}` URI template — and doubling every one of
        # them to survive `.format` turns a file whose entire purpose is to be read and
        # copied into something nobody can proofread.
        (root / "server.py").write_text(
            _PY_SERVER.replace("{title}", title), encoding="utf-8"
        )
        (root / "pyproject.toml").write_text(
            _PY_PYPROJECT.format(id=project_id, title=title, sdk=PY_SDK_SPEC),
            encoding="utf-8",
        )
    else:
        (root / "server.mjs").write_text(
            _NODE_SERVER.replace("{title}", title), encoding="utf-8"
        )
        (root / "package.json").write_text(
            _NODE_PACKAGE.format(id=project_id, title=title), encoding="utf-8"
        )
    (root / "README.md").write_text(
        _README.format(title=title, id=project_id, entry=project.entry),
        encoding="utf-8",
    )
    _manifest_path(root).write_text(
        json.dumps({"title": title, "template": template}, indent=2), encoding="utf-8"
    )

    # Registered disabled: the interpreter it points at does not exist until
    # provisioning finishes, and a server that fails on boot the moment you create it
    # reports a spawn error rather than "not provisioned yet".
    config = server_config(project)
    config["enabled"] = False
    cfg.save_server(config)

    with _lock:
        _projects[project_id] = project
    return project, None


def delete_project(project_id: str, *, delete_files: bool) -> bool:
    """Forget a project. Removes its server config; removes the tree only if asked."""
    project = _load(project_id)
    if project is None:
        return False
    cfg.delete_server(project_id)
    if delete_files:
        with _lock:
            _projects.pop(project_id, None)
        shutil.rmtree(project.root, ignore_errors=True)
    return True


def register(project: Project) -> None:
    """Put an unregistered project's server back in the list.

    The inverse of a Remove that kept the files — and the only reason an unregistered
    project is worth listing at all.
    """
    config = server_config(project)
    config["enabled"] = _runtime_present(project)
    cfg.save_server(config)


# --- provisioning -------------------------------------------------------------


def _uv() -> str | None:
    return shutil.which("uv")


def _npm() -> str | None:
    # npm is a `.cmd` shim on Windows, which `Popen` will not find without the
    # extension — `shutil.which` resolves it, a bare "npm" does not.
    return shutil.which("npm")


def toolchains() -> dict[str, bool]:
    """Which templates this machine can actually provision.

    Reported to the pane so a missing toolchain is named up front, rather than after
    the user has scaffolded a project that nothing here can build.
    """
    return {"hasUv": _uv() is not None, "hasNpm": _npm() is not None}


def provision_commands(project: Project) -> tuple[list[list[str]], str | None]:
    """The commands that make this project runnable, or why they can't be built."""
    if project.template == "python":
        uv = _uv()
        if uv is None:
            return [], "uv is not on PATH — install uv to provision a Python server."
        return (
            [
                [uv, "venv", ".venv"],
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python_path(project)),
                    PY_SDK_SPEC,
                ],
            ],
            None,
        )
    npm = _npm()
    if npm is None:
        return [], "npm is not on PATH — install Node.js to provision a Node server."
    return [[npm, "install"]], None


def _run(cmd: list[str], cwd: str, log: deque[str]) -> None:
    """Run one command to completion, streaming merged output into the ring."""
    log.append("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if stripped := line.rstrip():
            log.append(stripped)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} exited with {code}")


def _provision_blocking(project: Project) -> None:
    commands, err = provision_commands(project)
    if err:
        project.state = "error"
        project.error = err
        project.log.append(err)
        return
    try:
        for cmd in commands:
            _run(cmd, str(project.root), project.log)
    except Exception as exc:  # noqa: BLE001 — a failed install is a status, not a 500
        project.state = "error"
        project.error = f"{type(exc).__name__}: {exc}"
        project.log.append(project.error)
        logger.warning("mcp author: %s provisioning failed: %s", project.id, exc)
        return
    project.state = "ready"
    project.error = ""
    project.log.append("provisioned")


async def provision(project: Project) -> Project:
    """Create the project's runtime, then enable its server config.

    Runs on a worker thread: the installs are minutes-long and blocking, and spawning
    them with asyncio would fail outright on Windows under `--reload`.
    """
    import asyncio

    project.state = "provisioning"
    project.error = ""
    project.log.clear()
    await asyncio.to_thread(_provision_blocking, project)
    if project.state == "ready":
        # The config was saved disabled at scaffold time; now the interpreter exists.
        config = cfg.get_server(project.id) or server_config(project)
        config["enabled"] = True
        cfg.save_server(config)
    return project


# --- the edit loop ------------------------------------------------------------


async def restart(project: Project) -> str | None:
    """Restart the running session so an edit takes effect. Returns an error, if any.

    "Hot restart" is exactly a restart, not a reload: MCP has no mechanism for a client
    to make a server re-read its own source, and a `tools/list_changed` notification a
    server never sends won't arrive because we edited a file. Restarting the process is
    the only thing that actually makes the new tool list real, and because the bridge
    re-syncs from live state on every start, the agent's catalog follows.
    """
    from backend.modules.mcp.client import manager

    config = cfg.get_server(project.id)
    if config is None:
        return f"no MCP server '{project.id}'"
    if not config.get("enabled", True):
        return None
    runtime = await manager.start_server(project.id)
    if runtime is None:
        return f"no MCP server '{project.id}'"
    return runtime.error if runtime.state == "error" else None


_ENTRY_RE = re.compile(r"^(server\.py|server\.mjs)$")


def touches_runtime(project: Project, rel: str) -> bool:
    """Whether editing this file warrants a restart.

    A README change restarting a server would make every keystroke in the notes cost a
    process spawn; a `server.py` change that *doesn't* restart is a pane showing tools
    that no longer exist.
    """
    name = Path(rel).name
    if bool(_ENTRY_RE.match(name)):
        return True
    return Path(rel).suffix.lower() in {".py", ".mjs", ".js", ".ts", ".json"}
