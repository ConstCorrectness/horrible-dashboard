"""The local module bus: which horrible processes are running, and what they serve.

Every process that hosts modules (the dashboard, and each `horrible run <id>`) keeps
one record in `<data>/bus/proc-<pid>.json`:

    {v, pid, startedAt, kind, name, host, port, mcpPath, token,
     modules: [id], toolGroups: [{id, description}], toolsHash, heartbeatAt}

and rewrites it every `HEARTBEAT_S`. A peer reads the directory to find the others,
then talks to each one over MCP at `http://host:port/mcpPath` with the token (see
`module_mcp.py` and `bus_bridge.py`).

A record is **live** only if its heartbeat is fresh *and* the process it names is
still the process that wrote it. The second check is not paranoia: Windows reuses
pids quickly, and a crashed process's record with a recycled pid would otherwise look
alive forever. So `startedAt` is the writer's own process create time, and a reader
compares it with the create time of whatever process holds that pid now.

Readers delete records that fail either check, so a killed process disappears from
everyone's view within `STALE_S` without anyone having to clean up after it.

The token sits in the record in plain text. The bus directory is inside the node's
data dir, which only this OS user can read, and the MCP endpoint listens on loopback
only; the token keeps other users' processes and web pages out, which is its job.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend import jsonstore

logger = logging.getLogger(__name__)

HEARTBEAT_S = 5.0
STALE_S = 15.0
RECORD_VERSION = 1
MCP_PATH = "/bus/mcp"
# Create times round differently through JSON and between psutil calls; anything
# under this is the same process.
_CREATE_TIME_SLACK_S = 1.0


def bus_dir() -> Path:
    from backend import paths

    return paths.data_dir() / "bus"


def _create_time(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


@dataclass
class BusRecord:
    pid: int
    started_at: float
    kind: str  # "dashboard" | "standalone"
    name: str
    host: str
    port: int
    token: str
    modules: list[str] = field(default_factory=list)
    tool_groups: list[dict[str, str]] = field(default_factory=list)
    tools_hash: str = ""
    heartbeat_at: float = 0.0
    mcp_path: str = MCP_PATH

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.mcp_path}"

    def to_json(self) -> dict[str, Any]:
        return {
            "v": RECORD_VERSION,
            "pid": self.pid,
            "startedAt": self.started_at,
            "kind": self.kind,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "mcpPath": self.mcp_path,
            "token": self.token,
            "modules": list(self.modules),
            "toolGroups": list(self.tool_groups),
            "toolsHash": self.tools_hash,
            "heartbeatAt": self.heartbeat_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> BusRecord:
        return cls(
            pid=int(raw["pid"]),
            started_at=float(raw["startedAt"]),
            kind=str(raw.get("kind", "standalone")),
            name=str(raw.get("name", "")),
            host=str(raw.get("host", "127.0.0.1")),
            port=int(raw["port"]),
            token=str(raw["token"]),
            modules=[str(m) for m in raw.get("modules") or []],
            tool_groups=[dict(g) for g in raw.get("toolGroups") or []],
            tools_hash=str(raw.get("toolsHash", "")),
            heartbeat_at=float(raw.get("heartbeatAt", 0.0)),
            mcp_path=str(raw.get("mcpPath", MCP_PATH)),
        )

    def public(self) -> dict[str, Any]:
        """For listings: everything but the token."""
        out = self.to_json()
        out.pop("token", None)
        out["ageS"] = round(time.time() - self.heartbeat_at, 1)
        return out


def _path_for(pid: int) -> Path:
    return bus_dir() / f"proc-{pid}.json"


def is_live(rec: BusRecord, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if now - rec.heartbeat_at > STALE_S:
        return False
    created = _create_time(rec.pid)
    return created is not None and abs(created - rec.started_at) <= _CREATE_TIME_SLACK_S


def read_records(*, include_self: bool = False, prune: bool = True) -> list[BusRecord]:
    """Every live record on the bus. Dead and unreadable records are deleted."""
    out: list[BusRecord] = []
    directory = bus_dir()
    if not directory.is_dir():
        return out
    me = os.getpid()
    for path in directory.glob("proc-*.json"):
        try:
            text = jsonstore.read_text(path)
            rec = BusRecord.from_json(json.loads(text or ""))
        except (OSError, ValueError, KeyError, TypeError):
            # A half-written file cannot happen (writes are atomic), so this is
            # junk from an older format or a hand edit.
            if prune:
                with contextlib.suppress(OSError):
                    path.unlink()
            continue
        if not is_live(rec):
            if prune and rec.pid != me:
                with contextlib.suppress(OSError):
                    path.unlink()
            continue
        if rec.pid == me and not include_self:
            continue
        out.append(rec)
    return sorted(out, key=lambda r: (r.kind != "dashboard", r.name, r.pid))


def owner_of(module_id: str) -> BusRecord | None:
    """The live *other* process that hosts `module_id`, if any."""
    for rec in read_records():
        if module_id in rec.modules:
            return rec
    return None


def tools_hash(tools: list[dict[str, Any]]) -> str:
    blob = json.dumps(sorted(tools, key=lambda t: t["name"]), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class BusPublisher:
    """Keeps this process's record current until `stop()`.

    `describe` is called on every heartbeat and returns the fields that change
    (modules, tool groups, tools hash), so loading or unloading a module shows up on
    the bus within one beat without the module manager knowing the bus exists. Call
    `publish_now()` to skip the wait.
    """

    def __init__(
        self,
        *,
        kind: str,
        name: str,
        port: int | Any,
        describe: Any,
        host: str = "127.0.0.1",
    ) -> None:
        started = _create_time(os.getpid()) or time.time()
        self.record = BusRecord(
            pid=os.getpid(),
            started_at=started,
            kind=kind,
            name=name,
            host=host,
            port=port if isinstance(port, int) else 0,
            token=secrets.token_urlsafe(32),
        )
        # An int, or a callable read on every beat: the dashboard's real port is only
        # certain once uvicorn has bound (see backend/server_port.py).
        self._port = port
        self._describe = describe
        self._task: asyncio.Task[None] | None = None

    @property
    def token(self) -> str:
        return self.record.token

    def publish_now(self) -> None:
        if callable(self._port):
            self.record.port = int(self._port())
        fields = self._describe() or {}
        self.record.modules = list(fields.get("modules", []))
        self.record.tool_groups = list(fields.get("toolGroups", []))
        self.record.tools_hash = str(fields.get("toolsHash", ""))
        self.record.heartbeat_at = time.time()
        path = _path_for(self.record.pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            jsonstore.write_text(path, json.dumps(self.record.to_json(), indent=2))
        except OSError:
            # A reader holding the file open on Windows can fail the replace; the
            # next beat retries, and STALE_S is three beats long for this reason.
            logger.debug("bus: heartbeat write failed", exc_info=True)

    async def _beat(self) -> None:
        while True:
            try:
                self.publish_now()
            except Exception:  # noqa: BLE001 - the heartbeat must outlive a bad describe
                logger.exception("bus: heartbeat failed")
            await asyncio.sleep(HEARTBEAT_S)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._beat())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with contextlib.suppress(OSError):
            _path_for(self.record.pid).unlink()


# --- module claims ---------------------------------------------------------------------
#
# The bus record says which modules a process hosts, but it is published from the app
# lifespan, after modules have loaded. Two processes starting together would each check
# the bus, find nobody, and both load the module. So ownership is a separate, atomic
# claim taken *before* a module's setup runs: `<bus>/claim-<id>.json`, created with
# O_EXCL, so exactly one process can create it. A claim left by a dead process (crash,
# kill -9) is recognised the same way a stale record is, by pid and create time.


def _claim_path(module_id: str) -> Path:
    return bus_dir() / f"claim-{module_id}.json"


def _holder_name(pid: int) -> str:
    try:
        text = jsonstore.read_text(_path_for(pid))
        name = json.loads(text or "{}").get("name")
    except (OSError, ValueError):
        name = None
    return f"{name} (pid {pid})" if name else f"pid {pid}"


def claim(module_id: str) -> str | None:
    """Take the module for this process. Returns None on success, else who holds it."""
    path = _claim_path(module_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    mine = json.dumps({"pid": me, "startedAt": _create_time(me) or time.time()})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                held = json.loads(path.read_text(encoding="utf-8") or "{}")
                pid, started = int(held["pid"]), float(held["startedAt"])
            except (OSError, ValueError, KeyError, TypeError):
                # Being written this instant, or junk: junk is removed below.
                pid, started = -1, 0.0
            if pid == me:
                return None
            created = _create_time(pid) if pid > 0 else None
            if created is not None and abs(created - started) <= _CREATE_TIME_SLACK_S:
                return _holder_name(pid)
            with contextlib.suppress(OSError):
                path.unlink()  # its holder is gone; try again
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(mine)
        return None
    return "another process"


def release(module_id: str) -> None:
    """Give the module up, if this process holds it."""
    path = _claim_path(module_id)
    try:
        held = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return
    if held.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            path.unlink()
