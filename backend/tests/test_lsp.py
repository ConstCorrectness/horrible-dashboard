"""Tests for the LSP transport manager.

The manager is a dumb JSON-RPC pipe, so the deterministic surface worth covering
without a real language server is the **gate** (only known, installed servers
spawn) and the stdio **framing**. The full pipe is exercised at runtime against
pylsp/typescript-language-server.
"""

import asyncio
import json
import sys
from typing import Any

from backend.modules.lsp import manager as lsp_mod
from backend.modules.lsp import pyenv
from backend.modules.lsp.manager import LspManager, _frame


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def _events(conn: FakeConn, event: str) -> list[dict[str, Any]]:
    return [
        m["data"]
        for m in conn.sent
        if m.get("channel") == "lsp" and m.get("event") == event
    ]


def test_unknown_language_errors_without_spawning() -> None:
    conn = FakeConn()
    mgr = LspManager(conn)

    async def go() -> None:
        await mgr.handle(
            {
                "channel": "lsp",
                "event": "start",
                "data": {"sessionId": "s1", "languageId": "cobol"},
            }
        )

    asyncio.run(go())
    errors = _events(conn, "error")
    assert errors and errors[0]["sessionId"] == "s1"
    assert "cobol" in errors[0]["message"]
    assert not mgr.sessions  # nothing spawned


def test_missing_server_binary_errors(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(lsp_mod.shutil, "which", lambda _cmd: None)
    conn = FakeConn()
    mgr = LspManager(conn)

    async def go() -> None:
        await mgr.handle(
            {
                "channel": "lsp",
                "event": "start",
                "data": {"sessionId": "s2", "languageId": "python"},
            }
        )

    asyncio.run(go())
    assert _events(conn, "error")
    assert not mgr.sessions


def test_frame_uses_content_length_header() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    framed = _frame(payload)
    head, _, body = framed.partition(b"\r\n\r\n")
    assert head == b"Content-Length: " + str(len(body)).encode()
    assert json.loads(body) == payload


def test_python_prefers_basedpyright_then_pylsp() -> None:
    # Ordered candidates: basedpyright first (richer), pylsp as the fallback.
    candidates = lsp_mod.LSP_SERVERS["python"]
    assert candidates[0][0] == "basedpyright-langserver"
    assert candidates[-1][0] == "pylsp"


def test_resolve_server_falls_back_to_next_candidate(monkeypatch) -> None:  # noqa: ANN001
    # basedpyright missing → resolve_server skips it and picks pylsp.
    monkeypatch.setattr(
        lsp_mod.shutil,
        "which",
        lambda cmd: None if cmd == "basedpyright-langserver" else f"/usr/bin/{cmd}",
    )
    resolved = lsp_mod.resolve_server("python")
    assert resolved is not None
    exe, cmd = resolved
    assert cmd == ["pylsp"]
    assert exe.endswith("pylsp")


def test_resolve_server_unknown_language_is_none() -> None:
    assert lsp_mod.resolve_server("cobol") is None


def test_resolve_interpreter_prefers_nearest_venv(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    # A `.venv` up the tree wins over the system Python, whatever that is.
    rel = ".venv/Scripts" if sys.platform == "win32" else ".venv/bin"
    exe_name = "python.exe" if sys.platform == "win32" else "python"
    venv_bin = tmp_path / "proj" / rel
    venv_bin.mkdir(parents=True)
    exe = venv_bin / exe_name
    exe.write_text("")
    nested = tmp_path / "proj" / "pkg" / "sub"
    nested.mkdir(parents=True)
    monkeypatch.setattr(pyenv, "_system_python", lambda: "/sys/python3")
    assert pyenv.resolve_python_interpreter(str(nested)) == str(exe)


def test_resolve_interpreter_falls_back_to_system(monkeypatch) -> None:  # noqa: ANN001
    # No venv found → the system default interpreter.
    monkeypatch.setattr(pyenv, "_nearest_venv_python", lambda _start: None)
    monkeypatch.setattr(pyenv, "_system_python", lambda: "/sys/python3")
    assert pyenv.resolve_python_interpreter("/some/dir") == "/sys/python3"


def test_resolve_interpreter_none_start_uses_system(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(pyenv, "_system_python", lambda: "/sys/python3")
    assert pyenv.resolve_python_interpreter(None) == "/sys/python3"


def test_resolve_project_root_finds_marker(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "pyproject.toml").write_text("")
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    assert pyenv.resolve_project_root(str(nested)) == str(tmp_path)


def test_resolve_project_root_bare_dir_is_itself(tmp_path) -> None:  # noqa: ANN001
    # No marker up the tree (temp dirs have none) → the directory itself.
    sub = tmp_path / "loose"
    sub.mkdir()
    assert pyenv.resolve_project_root(str(sub)) == str(sub)


def test_installed_versions_parses_interpreter_output(monkeypatch) -> None:  # noqa: ANN001
    pyenv.installed_versions.cache_clear()

    class _Proc:
        stdout = '{"numpy": "2.2.6", "torch": "2.11.0"}'

    monkeypatch.setattr(pyenv.subprocess, "run", lambda *a, **k: _Proc())
    assert pyenv.installed_versions("/py/python3") == {
        "numpy": "2.2.6",
        "torch": "2.11.0",
    }


def test_installed_versions_no_interpreter_is_empty() -> None:
    assert pyenv.installed_versions(None) == {}


class _FakeStdout:
    """Immediately EOFs so the manager's reader thread exits cleanly."""

    def readline(self) -> bytes:
        return b""

    def read(self, _n: int) -> bytes:
        return b""


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = None
        self.stdout = _FakeStdout()

    def poll(self) -> int | None:
        return None

    def kill(self) -> None:
        pass


def test_started_event_carries_python_path(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        lsp_mod,
        "resolve_server",
        lambda _lang: ("/x/basedpyright", ["basedpyright-langserver", "--stdio"]),
    )
    monkeypatch.setattr(lsp_mod.subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(lsp_mod, "resolve_python_interpreter", lambda _d: "/py/python3")
    conn = FakeConn()
    mgr = LspManager(conn)

    async def go() -> None:
        await mgr.handle(
            {
                "channel": "lsp",
                "event": "start",
                "data": {"sessionId": "s1", "languageId": "python", "root": None},
            }
        )

    asyncio.run(go())
    started = _events(conn, "started")
    assert started and started[0]["sessionId"] == "s1"
    assert started[0]["pythonPath"] == "/py/python3"


def test_started_event_omits_python_path_for_non_python(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        lsp_mod,
        "resolve_server",
        lambda _lang: ("/x/rust-analyzer", ["rust-analyzer"]),
    )
    monkeypatch.setattr(lsp_mod.subprocess, "Popen", lambda *a, **k: _FakeProc())
    conn = FakeConn()
    mgr = LspManager(conn)

    async def go() -> None:
        await mgr.handle(
            {
                "channel": "lsp",
                "event": "start",
                "data": {"sessionId": "s2", "languageId": "rust", "root": None},
            }
        )

    asyncio.run(go())
    started = _events(conn, "started")
    assert started and "pythonPath" not in started[0]
