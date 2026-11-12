"""Mount the tools other processes on the bus serve, as local agent tools.

Every few seconds the bridge reads the bus (`bus.py`). For each live peer whose
`toolsHash` changed, it lists the peer's tools over MCP and registers a mirror
`AgentTool` per tool under the owner `bus:<pid>`. A mirror has the peer's own name
(`karaoke.status`), so it lands in the same group the module has in-process, and the
agent cannot tell the difference except that the call crosses a socket.

Rules that keep this safe and small:

- **In-process wins.** A tool name that already exists here (the module is loaded
  locally) is never mirrored. When the local copy is unloaded, the next poll mirrors
  the peer's.
- **Never always-on.** Mirrors always carry a group, so they load on demand and the
  agent's always-on tool budget does not grow with the number of peers.
- **Side effects are gated here.** A mirror keeps `side_effect` from the peer's
  `destructiveHint`, so the dashboard's permission gate asks before the call leaves.
- **Peers vanish cleanly.** A record that goes stale (process killed) or disappears
  (process stopped) drops its mirrors on the next poll.

Each call opens a short-lived MCP session: the peer's endpoint is stateless and on
loopback, so a session per call costs a few milliseconds and leaves nothing to
reconnect when a peer restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

from backend.kernel import bus
from backend.kernel.module_mcp import GROUP_META, SPECIFIER_META
from backend.sdk.registry import PluginRegistry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

POLL_S = 3.0
CALL_TIMEOUT_S = 120.0
LIST_TIMEOUT_S = 10.0


def owner_key(pid: int) -> str:
    return f"bus:{pid}"


def decode_result(text: str) -> Any:
    """Undo `module_mcp.encode_result`: JSON back to a value, else the text."""
    try:
        return json.loads(text)
    except ValueError:
        return text


async def _session_call(rec: bus.BusRecord, fn: Callable[[Any], Any]) -> Any:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {rec.token}"}
    # The endpoint is stateless, so there is no session to terminate on close.
    async with httpx.AsyncClient(headers=headers, timeout=CALL_TIMEOUT_S) as client:
        async with streamable_http_client(
            rec.url, http_client=client, terminate_on_close=False
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


async def list_peer_tools(rec: bus.BusRecord) -> list[Any]:
    async def go(session: Any) -> list[Any]:
        return list((await session.list_tools()).tools)

    return await asyncio.wait_for(_session_call(rec, go), timeout=LIST_TIMEOUT_S)


async def call_peer_tool(rec: bus.BusRecord, name: str, args: dict[str, Any]) -> Any:
    import mcp.types as types

    async def go(session: Any) -> Any:
        return await session.call_tool(name, args)

    try:
        result = await asyncio.wait_for(_session_call(rec, go), timeout=CALL_TIMEOUT_S)
    except TimeoutError:
        return {"error": f"{name} on {rec.name} timed out"}
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as a value
        return {"error": f"{rec.name} unreachable: {type(exc).__name__}: {exc}"}
    text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
    if result.isError:
        return {"error": text or "tool failed"}
    return decode_result(text)


class BusBridge:
    """Polls the bus and keeps the local registry's mirrors in step with it."""

    def __init__(
        self,
        reg: PluginRegistry,
        *,
        on_change: Callable[[], None] | None = None,
        include_self: bool = False,
    ) -> None:
        # include_self: mirror this process's own record too. Only a test wants that,
        # to run both ends of the bus in one process.
        self._include_self = include_self
        self._reg = reg
        self._on_change = on_change
        self._peers: dict[int, bus.BusRecord] = {}
        self._mirrored_hash: dict[int, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # --- reads --------------------------------------------------------------------

    def peers(self) -> list[bus.BusRecord]:
        return list(self._peers.values())

    def group_description(self, group: str) -> str | None:
        for rec in self._peers.values():
            for g in rec.tool_groups:
                if g.get("id") == group:
                    return str(g.get("description") or "") or None
        return None

    # --- sync ---------------------------------------------------------------------

    def _mirror(self, rec: bus.BusRecord, tool: Any) -> AgentTool | None:
        if tool.name in self._reg.agent_tools:
            # Something already answers to this name: a module loaded here, or
            # another peer mirrored first. In-process (or first) wins.
            return None
        schema = tool.inputSchema or {}
        meta = tool.meta or {}
        annotations = tool.annotations
        side_effect = bool(annotations and annotations.destructiveHint)

        async def handler(args: dict[str, Any], _name: str = tool.name) -> Any:
            live = self._peers.get(rec.pid)
            if live is None:
                return {"error": f"{_name}: {rec.name} is no longer running"}
            return await call_peer_tool(live, _name, args)

        return AgentTool(
            name=tool.name,
            description=f"{tool.description or ''} (runs in {rec.name})".strip(),
            handler=handler,
            parameters=dict(schema.get("properties") or {}),
            required=list(schema.get("required") or []),
            side_effect=side_effect,
            specifier_template=meta.get(SPECIFIER_META),
            group=str(meta.get(GROUP_META) or tool.name.split(".", 1)[0]),
        )

    def _drop(self, pid: int) -> None:
        self._reg.unregister_owner(owner_key(pid))
        self._peers.pop(pid, None)
        self._mirrored_hash.pop(pid, None)

    async def sync_once(self) -> bool:
        """One poll. Returns whether the set of mirrored tools changed."""
        async with self._lock:
            changed = False
            live = {
                r.pid: r
                for r in await asyncio.to_thread(
                    bus.read_records, include_self=self._include_self
                )
            }
            for pid in list(self._peers):
                if pid not in live:
                    logger.info("bus: %s (pid %s) left", self._peers[pid].name, pid)
                    self._drop(pid)
                    changed = True
            for pid, rec in live.items():
                self._peers[pid] = rec
                if self._mirrored_hash.get(pid) == rec.tools_hash:
                    continue
                try:
                    tools = await list_peer_tools(rec)
                except Exception as exc:  # noqa: BLE001 - a peer mid-start is normal
                    logger.debug("bus: list tools on %s failed: %s", rec.name, exc)
                    continue
                self._reg.unregister_owner(owner_key(pid))
                added = 0
                for tool in tools:
                    mirror = self._mirror(rec, tool)
                    if mirror is None:
                        continue
                    self._reg.agent_tools[mirror.name] = mirror
                    self._reg.own(owner_key(pid), "agent_tools", mirror.name)
                    added += 1
                self._mirrored_hash[pid] = rec.tools_hash
                changed = True
                logger.info(
                    "bus: mirrored %d tool(s) from %s (pid %s)", added, rec.name, pid
                )
            if changed and self._on_change is not None:
                with contextlib.suppress(Exception):
                    self._on_change()
            return changed

    def resync(self) -> None:
        """Forget the hashes so the next poll re-lists every peer. Call after a local
        module unloads: a name it held may now be free for a peer's mirror."""
        self._mirrored_hash.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self.sync_once()
            except Exception:  # noqa: BLE001 - the poll must survive a bad peer
                logger.exception("bus: sync failed")
            await asyncio.sleep(POLL_S)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for pid in list(self._peers):
            self._drop(pid)
