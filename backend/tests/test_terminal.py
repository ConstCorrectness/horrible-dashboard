"""Tests for the terminal session manager (D1).

The PTY lifecycle is driven through a fake backend so the tests are deterministic
and platform-independent; the real ptyprocess/pywinpty adapter is exercised at
runtime. The fake mirrors the read/write/setwinsize/terminate surface the manager
uses, with a blocking `read` (run in a thread by the pump) unblocked by EOF.
"""

import asyncio
import queue
from typing import Any

from backend.modules.terminal import pty as pty_mod
from backend.modules.terminal import shells as shells_mod
from backend.modules.terminal.manager import TerminalManager
from backend.modules.terminal.pty import default_shell


class FakePty:
    def __init__(self) -> None:
        self._q: queue.Queue[str | None] = queue.Queue()
        self.writes: list[str] = []
        self.size: tuple[int, int] | None = None
        self.terminated = False

    # control hook used by tests
    def feed(self, text: str) -> None:
        self._q.put(text)

    # PtyProcess surface
    def read(self, size: int = 1024) -> str:
        item = self._q.get()
        if item is None:
            raise EOFError
        return item

    def write(self, data: str) -> int:
        self.writes.append(data)
        return len(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        self.size = (rows, cols)

    def isalive(self) -> bool:
        return not self.terminated

    def terminate(self, force: bool = False) -> None:
        self.terminated = True
        self._q.put(None)  # unblock read with EOF


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self) -> list[tuple[str, dict[str, Any]]]:
        return [(s["event"], s["data"]) for s in self.sent]


async def _wait_for(conn: FakeConn, event: str, timeout: float = 2.0) -> dict[str, Any]:
    async def poll() -> dict[str, Any]:
        while True:
            for ev, data in conn.events():
                if ev == event:
                    return data
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), timeout)


def _manager() -> tuple[TerminalManager, FakeConn, list[FakePty]]:
    conn = FakeConn()
    spawned: list[FakePty] = []

    def spawn(argv, **kwargs):  # noqa: ANN001, ANN003
        pty = FakePty()
        pty.argv = list(argv)
        spawned.append(pty)
        return pty

    return TerminalManager(conn, spawn=spawn), conn, spawned


def test_start_emits_started_and_tracks_session() -> None:
    async def go() -> None:
        mgr, conn, _ = _manager()
        await mgr.handle(
            {"event": "start", "data": {"id": "t1", "cols": 80, "rows": 24}}
        )
        started = dict(conn.events())["started"]
        assert started["id"] == "t1"
        # The shell is echoed back; nothing requested one, so it is the default.
        assert started["shell"] == shells_mod.default_shell_id()
        assert "t1" in mgr.sessions
        await mgr.close_all()

    asyncio.run(go())


def test_output_is_pumped_to_connection() -> None:
    async def go() -> None:
        mgr, conn, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        spawned[0].feed("hello\r\n")
        out = await _wait_for(conn, "output")
        assert out == {"id": "t1", "data": "hello\r\n"}
        await mgr.close_all()

    asyncio.run(go())


def test_input_is_forwarded_to_pty() -> None:
    async def go() -> None:
        mgr, _, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "input", "data": {"id": "t1", "data": "ls\n"}})
        assert spawned[0].writes == ["ls\n"]
        await mgr.close_all()

    asyncio.run(go())


def test_resize_is_forwarded_to_pty() -> None:
    async def go() -> None:
        mgr, _, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle(
            {"event": "resize", "data": {"id": "t1", "rows": 30, "cols": 100}}
        )
        assert spawned[0].size == (30, 100)
        await mgr.close_all()

    asyncio.run(go())


def test_kill_terminates_and_emits_exit() -> None:
    async def go() -> None:
        mgr, conn, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "kill", "data": {"id": "t1"}})
        await _wait_for(conn, "exit")
        assert spawned[0].terminated
        assert "t1" not in mgr.sessions

    asyncio.run(go())


def test_duplicate_id_errors() -> None:
    async def go() -> None:
        mgr, conn, _ = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        errors = [d for ev, d in conn.events() if ev == "error"]
        assert errors and errors[0]["id"] == "t1"
        await mgr.close_all()

    asyncio.run(go())


def test_close_all_terminates_every_session() -> None:
    async def go() -> None:
        mgr, _, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1"}})
        await mgr.handle({"event": "start", "data": {"id": "t2"}})
        await mgr.close_all()
        assert all(p.terminated for p in spawned)
        assert mgr.sessions == {}

    asyncio.run(go())


def test_input_to_unknown_session_is_ignored() -> None:
    async def go() -> None:
        mgr, _, _ = _manager()
        # No exception for input to a session that doesn't exist.
        await mgr.handle({"event": "input", "data": {"id": "ghost", "data": "x"}})

    asyncio.run(go())


# --- default_shell resolution (these run on Windows CI too, so platform is faked) ---


def test_default_shell_windows(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(pty_mod.sys, "platform", "win32")
    assert default_shell() == "powershell.exe"


def test_default_shell_prefers_valid_shell_env(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(pty_mod.sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    monkeypatch.setattr(pty_mod.os.path, "isfile", lambda p: p == "/usr/bin/fish")
    assert default_shell() == "/usr/bin/fish"


def test_default_shell_falls_back_when_shell_unset(monkeypatch) -> None:  # noqa: ANN001
    # GUI-launched apps often have no $SHELL; pick the first shell that exists.
    monkeypatch.setattr(pty_mod.sys, "platform", "linux")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(pty_mod.os.path, "isfile", lambda p: p == "/bin/bash")
    assert default_shell() == "/bin/bash"


def test_default_shell_last_resort_is_sh(monkeypatch) -> None:  # noqa: ANN001
    # $SHELL set but invalid, and no common shell on disk → /bin/sh.
    monkeypatch.setattr(pty_mod.sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/does/not/exist")
    monkeypatch.setattr(pty_mod.shutil, "which", lambda c: None)
    monkeypatch.setattr(pty_mod.os.path, "isfile", lambda p: False)
    assert default_shell() == "/bin/sh"


# --- shell discovery -------------------------------------------------------
#
# The picker's catalog. These fake the platform and the filesystem so they run
# identically on every CI box.


def _slash(path: str) -> str:
    """Compare paths without caring which separator `Path` rendered.

    These tests fake Windows but run on POSIX CI too, where `Path` keeps forward
    slashes — so a literal comparison passes on one and fails on the other.
    """
    return path.replace("\\", "/")


def _fake_windows(monkeypatch, files: set[str], path_exes: dict[str, str]) -> None:  # noqa: ANN001
    """Pretend to be a Windows machine with exactly `files` on disk."""
    monkeypatch.setattr(shells_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        shells_mod.Path, "is_file", lambda self: str(self).replace("\\", "/") in files
    )
    monkeypatch.setattr(shells_mod.shutil, "which", lambda exe: path_exes.get(exe))
    # No WSL unless a test asks for it.
    monkeypatch.setattr(shells_mod, "_wsl_distros", lambda: ([], None))
    shells_mod.reset_cache()


def test_discovers_git_bash_by_absolute_path(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(
        monkeypatch,
        files={"C:/Program Files/Git/bin/bash.exe"},
        path_exes={"powershell.exe": "C:/Windows/System32/powershell.exe"},
    )
    found = {s.id: s for s in shells_mod.discover()}
    assert "git-bash" in found
    assert _slash(found["git-bash"].argv[0]) == "C:/Program Files/Git/bin/bash.exe"


def test_never_offers_the_wsl_shim_as_bash(monkeypatch) -> None:  # noqa: ANN001
    """The trap this module exists to avoid.

    `shutil.which("bash")` on Windows resolves to `C:\Windows\System32\bash.exe` —
    the WSL launcher, not Git Bash. Offering it under a bash label would hand the user
    a shell where `/mnt/c/...` is the filesystem and every cwd the app passes is wrong,
    with nothing anywhere saying so.
    """
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(
        monkeypatch,
        files=set(),  # no Git Bash anywhere
        path_exes={
            "bash": "C:/Windows/System32/bash.exe",
            "bash.exe": "C:/Windows/System32/bash.exe",
            "cmd.exe": "C:/Windows/System32/cmd.exe",
        },
    )
    found = shells_mod.discover()
    assert not any(s.kind == "bash" for s in found)
    assert not any("system32/bash.exe" in _slash(s.argv[0]).lower() for s in found if s.argv)


def test_wsl_distros_are_named_and_flagged(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(monkeypatch, files=set(), path_exes={})
    monkeypatch.setattr(shells_mod, "_wsl_distros", lambda: (["Ubuntu"], None))
    found = {s.id: s for s in shells_mod.discover()}
    assert "wsl:Ubuntu" in found
    # The path semantics warning is the entry's whole reason for carrying a note.
    assert "/mnt/c" in (found["wsl:Ubuntu"].note or "")


def test_could_not_ask_about_wsl_is_not_none_installed(monkeypatch) -> None:  # noqa: ANN001
    """Three states, not two — the hardware module's rule.

    "WSL is there but would not answer" must not render as "you have no WSL".
    """
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(monkeypatch, files=set(), path_exes={})
    monkeypatch.setattr(shells_mod, "_wsl_distros", lambda: ([], "WSL did not answer."))
    found = {s.id: s for s in shells_mod.discover()}
    assert "wsl:unknown" in found
    assert found["wsl:unknown"].note == "WSL did not answer."
    # Not launchable: it describes a gap in our knowledge, not a shell.
    assert not found["wsl:unknown"].argv
    assert shells_mod.is_known("wsl:unknown") is False


def test_resolve_refuses_a_path_and_falls_back(monkeypatch) -> None:  # noqa: ANN001
    """The security boundary. The wire carries an id from the discovered set.

    If `resolve` honoured a path, the terminal channel would be an arbitrary-exec
    route with a PTY attached, reachable by anything that can open the websocket.
    """
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(
        monkeypatch,
        files={"C:/Program Files/Git/bin/bash.exe"},
        path_exes={"powershell.exe": "C:/Windows/System32/powershell.exe"},
    )
    default = [default_shell()]
    assert shells_mod.resolve("C:/Windows/System32/calc.exe") == default
    assert shells_mod.resolve("../../../bin/sh") == default
    assert shells_mod.resolve("nonesuch") == default
    assert shells_mod.resolve(None) == default
    # And the one real id still works.
    assert _slash(shells_mod.resolve("git-bash")[0]) == "C:/Program Files/Git/bin/bash.exe"


def test_start_spawns_the_requested_shell(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(
        monkeypatch,
        files={"C:/Program Files/Git/bin/bash.exe"},
        path_exes={"powershell.exe": "C:/Windows/System32/powershell.exe"},
    )

    async def go() -> None:
        mgr, conn, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1", "shell": "git-bash"}})
        assert _slash(spawned[0].argv[0]) == "C:/Program Files/Git/bin/bash.exe"
        started = dict(conn.events())["started"]
        assert started["shell"] == "git-bash"
        # Honoured, so nothing to correct.
        assert "requestedShell" not in started
        await mgr.close_all()

    asyncio.run(go())


def test_start_reports_a_fallback_rather_than_hiding_it(monkeypatch) -> None:  # noqa: ANN001
    """A silent fallback leaves the pane captioned with a shell it is not running."""
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    _fake_windows(
        monkeypatch,
        files=set(),
        path_exes={"powershell.exe": "C:/Windows/System32/powershell.exe"},
    )

    async def go() -> None:
        mgr, conn, spawned = _manager()
        await mgr.handle({"event": "start", "data": {"id": "t1", "shell": "git-bash"}})
        assert spawned[0].argv == [default_shell()]
        started = dict(conn.events())["started"]
        assert started["requestedShell"] == "git-bash"
        assert started["shell"] != "git-bash"
        await mgr.close_all()

    asyncio.run(go())
