"""The `dash` SDK seeded into every REPL namespace.

A thin, **synchronous** Pythonic veneer over the same UI relay surface the agent
drives: every method turns into a tool call relayed to the originating browser,
where `executeTool` runs it against the registry + layout controller (see
packages/core/src/modules/agent/tool-exec.ts). The injected `call` blocks the
worker thread until the browser replies, so REPL code reads like ordinary
synchronous Python. See docs/modules/repl.md.
"""

from __future__ import annotations

from typing import Any, Protocol


class RelayCall(Protocol):
    """Sync bridge the manager injects: relay a tool call and block for its result."""

    def __call__(self, name: str, args: dict[str, Any]) -> Any: ...


class _Panes:
    """Open, close, and inspect panes (panels and widgets) in the active workspace."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def available(self) -> Any:
        """Every pane (panel + widget) that can be opened, with id and title."""
        return self._call("list_available_panes", {})

    def open_list(self) -> Any:
        """Panes currently open in the active workspace (type id, instanceId, …)."""
        return self._call("list_open_panes", {})

    def open(self, pane_id: str) -> Any:
        """Open a panel or widget by its id (from `available()`)."""
        return self._call("open_pane", {"id": pane_id})

    def close(self, pane_id: str) -> Any:
        """Close an open pane by its id."""
        return self._call("close_pane", {"id": pane_id})


class _Workspaces:
    """List, create, and switch the named workspace tabs."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def list(self) -> Any:
        return self._call("list_workspaces", {})

    def create(self, name: str) -> Any:
        """Create a new named workspace and switch to it."""
        return self._call("create_workspace", {"name": name})

    def switch(self, workspace_id: str) -> Any:
        return self._call("switch_workspace", {"id": workspace_id})


class Dash:
    """The dashboard handle. Drives panes/workspaces and reads live widget state;
    `call` is the escape hatch for any other relayed tool (widget agentTools,
    agent-exposed commands)."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call
        self.panes = _Panes(call)
        self.workspaces = _Workspaces(call)

    def context(self, instance_id: str) -> Any:
        """Read a live pane's current state/selection snapshot (instanceId from
        `panes.open_list()`)."""
        return self._call("get_pane_context", {"instanceId": instance_id})

    def call(self, name: str, **args: Any) -> Any:
        """Relay an arbitrary tool by name — covers every widget `agentTool` and
        agent-exposed command, so new capabilities are reachable without an SDK
        change."""
        return self._call(name, args)


def build_namespace(call: RelayCall) -> dict[str, Any]:
    """The starting globals for a REPL session: just `dash`."""
    return {"dash": Dash(call)}


__all__ = ["Dash", "RelayCall", "build_namespace"]
