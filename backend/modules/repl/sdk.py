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
    """Open, close, and inspect active panes (layout slots hosting views) in the active workspace."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def available(self) -> Any:
        """Every view (panel + widget view definition) that can be opened into a pane, with id and title."""
        return self._call("list_available_panes", {})

    def open_list(self) -> Any:
        """Active pane instances currently open in the active workspace (view id, instanceId, …)."""
        return self._call("list_open_panes", {})

    def open(self, view_id: str) -> Any:
        """Open a view (panel or widget) in a new or focused pane by its view ID (from `available()`)."""
        return self._call("open_pane", {"id": view_id})

    def close(self, instance_id: str) -> Any:
        """Close an active pane instance by its instance ID (from `open_list()`)."""
        return self._call("close_pane", {"id": instance_id})


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
    """The dashboard handle. Drives workspace layouts, manages active panes, and reads live view/pane state;
    `call` is the escape hatch for any other relayed tool (view-specific agentTools,
    agent-exposed commands)."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call
        self.panes = _Panes(call)
        self.workspaces = _Workspaces(call)

    def context(self, instance_id: str) -> Any:
        """Read an active pane instance's current state/selection snapshot (instanceId from
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
