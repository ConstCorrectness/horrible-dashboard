"""Agent Client Protocol (ACP) client session implementation.

Manages an external autonomous agent child process (e.g. Claude Code via
`@zed-industries/claude-code-acp`, Codex, or Gemini ACP) communicating over
JSON-RPC 2.0 stdio pipes.

Exposes `AcpClientSession` and `run_acp_turn`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from pathlib import Path
from typing import Any, Callable, Coroutine

from backend import paths
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

DEFAULT_ACP_COMMAND = "claude-code-acp"


class AcpClientSession:
    """A live ACP agent subprocess and client session."""

    def __init__(
        self,
        command: str | list[str] | None = None,
        cwd: Path | str | None = None,
    ) -> None:
        raw_cmd = command or str(get_value("agent.acp.command", DEFAULT_ACP_COMMAND))
        if isinstance(raw_cmd, str):
            self.command = shlex.split(raw_cmd)
        else:
            self.command = list(raw_cmd)
        self.cwd = Path(cwd) if cwd else (paths.repo_root() or Path.cwd())
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._req_id = 0
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self.session_id: str | None = None
        self.agent_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self._on_token: Callable[[str], Coroutine[Any, Any, None]] | None = None
        self._on_reasoning: Callable[[str], Coroutine[Any, Any, None]] | None = None
        self._on_tool_call: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None = None

    async def start(self) -> None:
        """Spawn the agent child process and start the JSON-RPC reader loop."""
        if self._proc is not None:
            return
        logger.info("Spawning ACP agent process: %s (cwd=%s)", self.command, self.cwd)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd),
            )
        except Exception as exc:
            logger.warning("Failed to start ACP agent command %s: %s", self.command, exc)
            raise RuntimeError(f"Cannot start ACP agent {self.command[0]}: {exc}") from exc

        self._reader_task = asyncio.create_task(self._read_loop())

    async def _send_json(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("ACP agent process is not running")
        payload = json.dumps(msg).encode("utf-8") + b"\n"
        self._proc.stdin.write(payload)
        await self._proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC 2.0 request and await the reply."""
        self._req_id += 1
        req_id = self._req_id
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        await self._send_json(msg)
        return await fut

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC 2.0 notification."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        await self._send_json(msg)

    async def initialize(self) -> dict[str, Any]:
        """Perform the ACP protocol initialization handshake."""
        res = await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "horrible-dashboard", "version": "0.3.1"},
                "capabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
        )
        self.agent_info = res.get("agentInfo") or {}
        self.capabilities = res.get("capabilities") or {}
        return res

    async def new_session(self) -> str:
        """Create a new ACP session scoped to the workspace directory."""
        res = await self.request("session/new", {"cwd": str(self.cwd), "mcpServers": []})
        self.session_id = str(res.get("sessionId", ""))
        return self.session_id

    async def prompt(
        self,
        prompt_text: str,
        on_token: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        on_reasoning: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> dict[str, Any]:
        """Send a prompt turn to the agent and stream updates."""
        if not self.session_id:
            await self.initialize()
            await self.new_session()

        self._on_token = on_token
        self._on_reasoning = on_reasoning
        self._on_tool_call = on_tool_call

        try:
            res = await self.request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [{"type": "text", "text": prompt_text}],
                },
            )
            return res
        finally:
            self._on_token = None
            self._on_reasoning = None
            self._on_tool_call = None

    async def cancel(self) -> None:
        """Cancel the in-flight prompt turn."""
        if self.session_id:
            await self.notify("session/cancel", {"sessionId": self.session_id})

    async def close(self) -> None:
        """Terminate the agent subprocess."""
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except Exception:
                if self._proc.poll() is None:
                    self._proc.kill()
            self._proc = None
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

    # --- JSON-RPC 2.0 Inbound Handling -------------------------------------

    async def _read_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        while True:
            line_bytes = await self._proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                logger.debug("Non-JSON line from ACP process: %r", line)
                continue
            await self._dispatch_message(msg)

    async def _dispatch_message(self, msg: dict[str, Any]) -> None:
        # Check if response to a pending request
        msg_id = msg.get("id")
        if msg_id is not None and ("result" in msg or "error" in msg):
            fut = self._pending_requests.pop(int(msg_id), None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(f"ACP RPC Error: {err}"))
                else:
                    fut.set_result(msg.get("result"))
            return

        method = msg.get("method")
        params = msg.get("params") or {}

        # Handle inbound request from Agent
        if msg_id is not None and method:
            await self._handle_agent_request(int(msg_id), method, params)
            return

        # Handle inbound notification from Agent
        if method == "session/update":
            await self._handle_session_update(params)

    async def _handle_agent_request(self, req_id: int, method: str, params: dict[str, Any]) -> None:
        """Handle client methods requested by the ACP agent."""
        try:
            if method == "fs/read_text_file":
                rel_path = params.get("path", "")
                target = (self.cwd / rel_path).resolve()
                if not str(target).startswith(str(self.cwd.resolve())):
                    raise PermissionError(f"Access denied outside workspace: {rel_path}")
                content = target.read_text(encoding="utf-8")
                await self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {"content": content}})
                return

            if method == "fs/write_text_file":
                rel_path = params.get("path", "")
                content = params.get("content", "")
                target = (self.cwd / rel_path).resolve()
                if not str(target).startswith(str(self.cwd.resolve())):
                    raise PermissionError(f"Access denied outside workspace: {rel_path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                await self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}})
                return

            if method == "session/request_permission":
                # By default, approve in standard mode or consult permissions
                await self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {"outcome": "accepted"}})
                return

            if method == "terminal/create":
                cmd = params.get("command", "")
                args = params.get("args") or []
                full_cmd = [cmd, *args] if args else shlex.split(cmd)
                proc = await asyncio.create_subprocess_exec(
                    *full_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.cwd),
                )
                stdout, stderr = await proc.communicate()
                out_text = (stdout + stderr).decode("utf-8", errors="replace")
                await self._send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"terminalId": f"term-{proc.pid}", "output": out_text, "exitCode": proc.returncode},
                })
                return

            # Unknown method
            await self._send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
        except Exception as exc:
            logger.exception("Error handling ACP request %s: %s", method, exc)
            await self._send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            })

    async def _handle_session_update(self, params: dict[str, Any]) -> None:
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")

        if kind == "agent_message_chunk":
            delta = update.get("delta", "")
            if delta and self._on_token:
                await self._on_token(delta)
        elif kind == "thought_chunk":
            delta = update.get("delta", "")
            if delta and self._on_reasoning:
                await self._on_reasoning(delta)
        elif kind == "tool_call":
            name = update.get("name", "tool")
            tool_params = update.get("params") or {}
            if self._on_tool_call:
                await self._on_tool_call(name, tool_params)


# --- Singleton Session Map --------------------------------------------------

_active_acp_sessions: dict[str, AcpClientSession] = {}


def get_acp_session(session_id: str = "default") -> AcpClientSession:
    if session_id not in _active_acp_sessions:
        _active_acp_sessions[session_id] = AcpClientSession()
    return _active_acp_sessions[session_id]


async def run_acp_turn(
    conn: Any,
    turn_id: str,
    agent_id: str,
    prompt: str,
) -> None:
    """Run an agent chat turn driven by an external ACP agent."""
    def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"channel": "agent", "event": event, "data": data}

    session = get_acp_session(f"{agent_id}:{turn_id}")
    accumulated_text: list[str] = []
    try:
        await session.start()
        await session.initialize()
        await session.new_session()

        async def on_token(delta: str) -> None:
            accumulated_text.append(delta)
            await conn.send_json(_evt("token", {"turnId": turn_id, "agentId": agent_id, "delta": delta}))

        async def on_reasoning(delta: str) -> None:
            await conn.send_json(_evt("reasoning", {"turnId": turn_id, "agentId": agent_id, "delta": delta}))

        res = await session.prompt(prompt, on_token=on_token, on_reasoning=on_reasoning)
        full_text = "".join(accumulated_text) or (res.get("stopReason", "") if isinstance(res, dict) else "")
        await conn.send_json(_evt("answer", {"turnId": turn_id, "agentId": agent_id, "text": full_text}))
        await conn.send_json(_evt("done", {"turnId": turn_id, "agentId": agent_id}))
    except asyncio.CancelledError:
        logger.info("ACP turn %s cancelled", turn_id)
        await session.cancel()
        await conn.send_json(_evt("done", {"turnId": turn_id, "agentId": agent_id}))
        raise
    except Exception as exc:
        logger.exception("ACP turn execution failed: %s", exc)
        await conn.send_json(
            _evt(
                "error",
                {
                    "turnId": turn_id,
                    "agentId": agent_id,
                    "message": f"ACP Agent Error: {exc}",
                },
            )
        )
        await conn.send_json(_evt("done", {"turnId": turn_id, "agentId": agent_id}))

