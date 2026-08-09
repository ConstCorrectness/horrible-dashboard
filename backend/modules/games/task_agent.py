"""The node's long-horizon agent for code tasks (bug hunt, and any `payload:
"files"` open action).

Unlike the board policy's short per-move loop, this is a working session: the
agent reads the repo, edits files in an in-memory workspace, runs the **visible**
tests locally (the shared `verify` runner), iterates, and finally submits. Its
built-in tools are the workspace; the player's loadout tools ride along as extra
tools, and every step flows through the same trace sink as `AgentPolicy` — which
makes bug-hunt replays genuinely worth watching.

The agent only ever runs the *visible* tests (the server owns the hidden ones and
the authoritative grade), so a node can't peek at what it isn't shown.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from backend.games_engine import verify
from backend.modules.games.loadout import HarnessRuntime, get_llm_harness
from backend.modules.games.policy import ChatFn, TraceFn, _clip

logger = logging.getLogger(__name__)

MAX_TASK_ROUNDS = 32
SUBMIT_TOOL = "task.submit"


def _builtin_tools() -> list[dict[str, Any]]:
    def fn(
        name: str, desc: str, props: dict[str, Any], required: list[str]
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }

    return [
        fn("task.listFiles", "List the workspace file paths.", {}, []),
        fn(
            "task.readFile",
            "Read one workspace file's current contents.",
            {"path": {"type": "string"}},
            ["path"],
        ),
        fn(
            "task.writeFile",
            "Replace one workspace file's contents (create if new).",
            {"path": {"type": "string"}, "content": {"type": "string"}},
            ["path", "content"],
        ),
        fn(
            "task.runTests",
            "Run the visible test suite against the current workspace and see the output.",
            {},
            [],
        ),
        fn(
            SUBMIT_TOOL,
            "Submit the current workspace files for authoritative grading. Do this once the visible tests pass.",
            {},
            [],
        ),
    ]


class TaskAgent:
    """One code-task session. `chat_fn` is the model client; injectable for tests."""

    def __init__(
        self,
        *,
        chat_fn: ChatFn,
        game_id: str,
        trace: TraceFn | None = None,
        run_tests: Callable[[dict[str, str]], Awaitable[Any]] | None = None,
    ) -> None:
        self._chat = chat_fn
        self._game_id = game_id
        self._trace = trace
        self._run_tests = run_tests or self._local_tests
        self._files: dict[str, str] = {}
        self._visible_tests: dict[str, str] = {}

    def _emit(self, kind: str, **fields: Any) -> None:
        if self._trace is None:
            return
        try:
            self._trace({"kind": kind, **fields})
        except Exception:
            logger.debug("task trace sink failed", exc_info=True)

    async def _local_tests(self, files: dict[str, str]) -> Any:
        """Run the visible suite in a worker thread (verify is synchronous)."""
        import asyncio

        return await asyncio.to_thread(
            verify.run_python_job, {**files, **self._visible_tests}
        )

    async def run(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Drive the session and return the `{files}` payload to submit."""
        self._files = dict(observation.get("files") or {})
        self._visible_tests = {
            str(k): str(v) for k, v in (observation.get("visible_tests") or {}).items()
        }
        runtime = HarnessRuntime(get_llm_harness(self._game_id))
        tools = _builtin_tools() + runtime.provider_tools()

        prior = observation.get("attempts") or []
        feedback = ""
        if prior:
            last = prior[-1]
            feedback = (
                f"\n\nYour last submission failed ({last.get('passed', 0)} passed, "
                f"{last.get('failed', 0)} failed):\n{str(last.get('output', ''))[:1500]}"
            )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are fixing a bug in a small Python repo. Read the files, "
                    "find the defect, edit files with task.writeFile, run task.runTests "
                    "until the visible tests pass, then call task.submit. Keep going "
                    "until the tests are green.\n\n" + runtime.loadout.context
                ).strip(),
            },
            {
                "role": "user",
                "content": (
                    f"Task: {observation.get('description', '')}\n\n"
                    f"Files: {json.dumps(list(self._files))}\n"
                    f"Visible tests: {json.dumps(list(self._visible_tests))}"
                    f"{feedback}\n\nStart by reading the code."
                ),
            },
        ]

        for _ in range(MAX_TASK_ROUNDS):
            result = await self._chat(messages, tools)
            messages.append(
                getattr(result, "assistant_message", None)
                or {"role": "assistant", "content": getattr(result, "content", "")}
            )
            calls = list(getattr(result, "tool_calls", []) or [])
            self._emit(
                "assistant",
                content=_clip(getattr(result, "content", "") or ""),
                tool_calls=[
                    {"name": c.name, "arguments": _clip(c.arguments)} for c in calls
                ],
            )
            if not calls:
                break
            for call in calls:
                if call.name == SUBMIT_TOOL:
                    self._emit("chose", action_id="submit")
                    return {"files": self._files}
                out = await self._dispatch(call, runtime)
                messages.append(
                    {
                        "role": "tool",
                        "name": call.name,
                        "tool_call_id": getattr(call, "id", call.name),
                        "content": json.dumps(out, default=str),
                    }
                )
                self._emit("tool_result", name=call.name, result=_clip(out))

        # Ran out of rounds without an explicit submit — submit what we have.
        self._emit("chose", action_id="submit")
        return {"files": self._files}

    async def _dispatch(self, call: Any, runtime: HarnessRuntime) -> Any:
        name, args = call.name, call.arguments
        if name == "task.listFiles":
            return {"files": list(self._files)}
        if name == "task.readFile":
            path = str(args.get("path") or "")
            return {"path": path, "content": self._files.get(path, "")}
        if name == "task.writeFile":
            path = str(args.get("path") or "")
            self._files[path] = str(args.get("content") or "")
            return {"ok": True, "path": path}
        if name == "task.runTests":
            result = await self._run_tests(dict(self._files))
            return {
                "green": getattr(result, "green", False),
                "passed": getattr(result, "passed", 0),
                "failed": getattr(result, "failed", 0),
                "output": (
                    getattr(result, "stdout", "") + getattr(result, "stderr", "")
                )[-2000:],
            }
        if runtime.has(name):
            return await runtime.call(name, args, {"files": self._files})
        return {"error": f"unknown tool {name}"}
