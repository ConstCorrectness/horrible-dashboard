"""`horrible`: run a module on its own, and look at the module bus.

    python -m backend.cli run karaoke [--port 0] [--ui auto|dev|dist|none] [--open]
    python -m backend.cli modules list
    python -m backend.cli bus ls

(`pnpm horrible ...` runs the same thing.)

`run` starts a slim backend (backend/kernel/slim_app.py) hosting the named modules
and whatever they depend on, joins the local module bus, and serves the module shell:
the built page from `apps/module-shell/dist` when there is one (`--ui dist`), or a
Vite dev server for it (`--ui dev`). `auto` picks dist when it exists.

The runner and the dashboard share the node's data dir. That is how they find each
other (`<data>/bus`), and it is why a module running here is refused by the
dashboard (and the other way round) until one of them stops: one process owns a
module's state.

No `--reload` and no asyncio subprocesses, on purpose: under uvicorn's reloader on
Windows, asyncio cannot spawn processes at all (see the memory note on it). The Vite
child is a plain `Popen`, and on Windows its whole tree is killed on exit, because
killing `pnpm` alone leaves the `node` it started holding the port.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_DIR = REPO / "apps" / "module-shell"


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def _free_port(host: str) -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _die_with_children() -> None:
    """Windows: put this process in a kill-on-close Job Object, before spawning anything.

    Every child (the `pnpm` shim, the `node` running Vite) inherits the job, and when
    this process ends the last handle to the job closes and Windows kills them all,
    however it ended: Ctrl+C, a crash, Task Manager. `atexit` covers only the first.
    Nested jobs are fine on Windows 8+; if it fails anyway, the atexit kill remains.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declared, not left to ctypes' defaults: GetCurrentProcess() returns the
    # pseudo-handle -1, which as a default 32-bit int return is not a valid 64-bit
    # handle, and every call below would fail without a word.
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", _Basic),
            ("Io", _Io),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _Extended()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)  # 9: ExtendedLimitInformation
    )
    if ok and kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        # Deliberately never closed: the handle lives exactly as long as we do.
        globals()["_JOB_HANDLE"] = job


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()


def _start_vite(
    module_id: str, backend_port: int, host: str
) -> tuple[subprocess.Popen[bytes], int]:
    ui_port = int(os.environ.get("MODULE_SHELL_PORT") or 0) or _free_port(host)
    env = {
        **os.environ,
        "HORRIBLE_DEV_BACKEND_PORT": str(backend_port),
        "MODULE_SHELL_PORT": str(ui_port),
        "HORRIBLE_DEV_HOST": host,
    }
    cmd = ["pnpm", "--filter", "@horrible/module-shell", "dev"]
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        cmd,
        cwd=REPO,
        env=env,
        shell=sys.platform == "win32",  # pnpm is a .cmd shim on Windows
    )
    atexit.register(_kill_tree, proc)
    return proc, ui_port


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    from backend.kernel.slim_app import SHELL_DIST, build_app

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    sock = _bind(args.host, args.port)
    port = int(sock.getsockname()[1])
    ui = args.ui
    if ui == "auto":
        ui = "dist" if (SHELL_DIST / "index.html").is_file() else "dev"
    app = build_app(
        args.modules,
        port=port,
        shell_dist=SHELL_DIST if ui == "dist" else None,
    )
    module = args.modules[0]
    url = f"http://{args.host}:{port}/?module={module}"
    vite = None
    if ui == "dev":
        _die_with_children()
        vite, ui_port = _start_vite(module, port, args.host)
        url = f"http://{args.host}:{ui_port}/?module={module}"
    print(f"\n  horrible run {' '.join(args.modules)}")
    print(f"  backend  http://{args.host}:{port}  (bus MCP at /bus/mcp)")
    if ui != "none":
        print(f"  open     {url}\n")
        if args.open:
            # Give Vite a moment to bind before the browser asks it for the page.
            if vite is not None:
                time.sleep(2.0)
            webbrowser.open(url)
    config = uvicorn.Config(app, log_level="info", lifespan="on")
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        if vite is not None:
            _kill_tree(vite)
    return 0


def cmd_modules_list(_args: argparse.Namespace) -> int:
    from backend.kernel.module_manager import ModuleManager

    mgr = ModuleManager()
    mgr.discover()
    rows = mgr.describe_all()["modules"]
    disabled = mgr.disabled()
    for m in rows:
        flag = "disabled" if m["id"] in disabled else "enabled"
        groups = ",".join(g["id"] for g in m.get("toolGroups") or []) or "-"
        print(
            f"{m['id']:<20} {m['version']:<8} {m['source']:<10} {flag:<9} tools:{groups}"
        )
    return 0


def cmd_bus_ls(args: argparse.Namespace) -> int:
    from backend.kernel import bus

    records = bus.read_records(include_self=True)
    if args.json:
        print(json.dumps([r.public() for r in records], indent=2))
        return 0
    if not records:
        print("nothing on the bus")
        return 0
    for r in records:
        mods = ",".join(r.modules) or "-"
        print(
            f"{r.kind:<11} {r.name:<16} pid {r.pid:<7} port {r.port:<6} "
            f"modules:{mods}  beat {time.time() - r.heartbeat_at:.0f}s ago"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horrible")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run modules on their own, joined to the bus")
    run.add_argument(
        "modules", nargs="+", help="module ids (the first is the one shown)"
    )
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=0, help="0 picks a free port")
    run.add_argument("--ui", choices=["auto", "dev", "dist", "none"], default="auto")
    run.add_argument("--open", action="store_true", help="open the page in a browser")
    run.set_defaults(fn=cmd_run)

    modules = sub.add_parser("modules", help="module packages")
    msub = modules.add_subparsers(dest="action", required=True)
    msub.add_parser("list").set_defaults(fn=cmd_modules_list)

    busp = sub.add_parser("bus", help="the local module bus")
    bsub = busp.add_subparsers(dest="action", required=True)
    ls = bsub.add_parser("ls")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(fn=cmd_bus_ls)

    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
