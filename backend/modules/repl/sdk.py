"""The `dash` SDK seeded into every REPL namespace.

`dash` is the Python handle for driving the running app. Two kinds of surface
hang off it:

* **UI facades** (`panes`, `workspaces`, `layout`, `context`, `call`) — a thin,
  **synchronous** veneer over the same relay the agent drives: each method becomes
  a tool call relayed to the originating browser, where `executeTool` runs it
  against the registry + layout controller (packages/core/src/modules/agent/
  tool-exec.ts). The injected `call` blocks the worker thread until the browser
  replies, so REPL code reads like ordinary blocking Python.
* **Backend-local facades** (`io`, `settings`) — read/write backend state directly
  (no browser round-trip), since the REPL *runs in the backend*. These are fast and
  work even with no browser attached.

`dash.help()` prints the whole surface, introspected live so it never drifts. See
docs/architecture/python-sdk.md.
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
        """Every view (panel + widget) that can be opened into a pane, with id, title, and
        groupId (its panel group's primary view id, if it belongs to one — see `group()`)."""
        return self._call("list_available_panes", {})

    def open_list(self) -> Any:
        """Active pane instances currently open in the active workspace (view id, instanceId,
        groupId, …)."""
        return self._call("list_open_panes", {})

    def open(self, view_id: str) -> Any:
        """Open a view (panel or widget) in a new or focused pane by its view ID (from `available()`)."""
        return self._call("open_pane", {"id": view_id})

    def close(self, instance_id: str) -> Any:
        """Close an active pane instance by its instance ID (from `open_list()`)."""
        return self._call("close_pane", {"id": instance_id})

    def group(self, view_id: str) -> Any:
        """The panel group `view_id` belongs to (primary hub + companion views toggled
        from its companion strip), or `{"groupId": None}` if it isn't grouped."""
        return self._call("get_pane_group", {"id": view_id})


class _Workspaces:
    """List, create, and switch the named workspace tabs."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def list(self) -> Any:
        """The workspace tabs and which one is active."""
        return self._call("list_workspaces", {})

    def create(self, name: str) -> Any:
        """Create a new named workspace and switch to it."""
        return self._call("create_workspace", {"name": name})

    def switch(self, workspace_id: str) -> Any:
        """Switch to a workspace tab by id (from `list()`)."""
        return self._call("switch_workspace", {"id": workspace_id})


class _Layout:
    """Rearrange open panes — split, move, float, and maximize — the same verbs the
    agent's layout control uses. Pane targets are **instance ids** (from
    `dash.panes.open_list()`)."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def split(
        self, instance_id: str, direction: str, view_id: str | None = None
    ) -> Any:
        """Split a pane (`left`/`right`/`up`/`down`); `view_id` to open a different
        view in the new region, else the pane's own view is duplicated."""
        args: dict[str, Any] = {"instanceId": instance_id, "direction": direction}
        if view_id is not None:
            args["paneId"] = view_id
        return self._call("split_pane", args)

    def move(self, instance_id: str, reference: str, direction: str) -> Any:
        """Move a pane next to `reference` (`left`/`right`/`above`/`below`/`within`)."""
        return self._call(
            "move_pane",
            {"instanceId": instance_id, "reference": reference, "direction": direction},
        )

    def resize(
        self, instance_id: str, width: int | None = None, height: int | None = None
    ) -> Any:
        """Resize a pane (pixels)."""
        return self._call(
            "resize_pane", {"instanceId": instance_id, "width": width, "height": height}
        )

    def float(self, instance_id: str) -> Any:
        """Pop a pane out into a floating window."""
        return self._call("float_pane", {"instanceId": instance_id})

    def dock(self, instance_id: str) -> Any:
        """Dock a floating pane back into the grid."""
        return self._call("dock_pane", {"instanceId": instance_id})

    def maximize(self, instance_id: str) -> Any:
        """Maximize a pane within its group."""
        return self._call("maximize_pane", {"instanceId": instance_id})

    def restore(self, instance_id: str) -> Any:
        """Restore a maximized pane."""
        return self._call("restore_pane", {"instanceId": instance_id})


class _Io:
    """Read the live I/O telemetry — the same stream the observability panel shows
    (HTTP in/out and `/ws` frames). Backend-local: no browser round-trip."""

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """The most recent I/O events (newest last), each as a dict."""
        from backend.modules.telemetry.recorder import recorder

        events = recorder.recent()
        sliced = events[-limit:] if limit and limit > 0 else events
        return [e.model_dump() for e in sliced]

    def errors(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent events that failed (error set, or HTTP status ≥ 400)."""
        from backend.modules.telemetry.recorder import recorder

        bad = [
            e
            for e in recorder.recent()
            if e.error or (e.status is not None and e.status >= 400)
        ]
        sliced = bad[-limit:] if limit and limit > 0 else bad
        return [e.model_dump() for e in sliced]

    def clear(self) -> dict[str, Any]:
        """Empty the telemetry ring buffer."""
        from backend.modules.telemetry.recorder import recorder

        recorder.clear()
        return {"ok": True, "cleared": True}


class _Settings:
    """Read and write app settings (the same values the Settings page edits).
    Backend-local: writes persist immediately to the settings file."""

    def get(self, key: str, default: Any = None) -> Any:
        """The value for `key` (the persisted override, else `default`)."""
        from backend.modules.settings import get_value

        return get_value(key, default)

    def set(self, key: str, value: Any) -> dict[str, Any]:
        """Persist an override for `key`."""
        from backend.modules.settings.routes import set_value

        set_value(key, value)
        return {"ok": True, "key": key, "value": value}

    def all(self) -> dict[str, Any]:
        """Every persisted override, as a flat key→value dict."""
        from backend.modules.settings.routes import _read

        return _read()


class Dash:
    """The dashboard handle. Drives workspace layouts, manages and rearranges panes,
    reads live pane state and I/O telemetry, and reads/writes settings. `call` is the
    escape hatch for any other relayed tool. Run `dash.help()` for the full surface."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call
        self.panes = _Panes(call)
        self.workspaces = _Workspaces(call)
        self.layout = _Layout(call)
        self.io = _Io()
        self.settings = _Settings()

    def context(self, instance_id: str) -> Any:
        """Read an active pane instance's current state/selection snapshot (instanceId from
        `panes.open_list()`)."""
        return self._call("get_pane_context", {"instanceId": instance_id})

    def call(self, name: str, **args: Any) -> Any:
        """Relay an arbitrary tool by name — covers every widget `agentTool` and
        agent-exposed command, so new capabilities are reachable without an SDK
        change."""
        return self._call(name, args)

    def help(self) -> None:
        """Print the whole `dash` surface (facades, methods, and one-line docs)."""
        print(render_help(self))

    def __repr__(self) -> str:
        return "<dash — the dashboard handle; run dash.help() for the full surface>"


def _first_doc_line(obj: Any) -> str:
    doc = (getattr(obj, "__doc__", "") or "").strip()
    return " ".join(doc.split("\n")[0].split()) if doc else ""


def _public_methods(obj: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        member = getattr(obj, name)
        if callable(member):
            out.append((name, _first_doc_line(member)))
    return out


def render_help(dash: Dash) -> str:
    """Introspect a `Dash` instance into a readable cheat-sheet (kept in lockstep
    with the code because it reads the live objects)."""
    lines = ["dash — drive horrible-dashboard from Python.", ""]
    # Sub-facades (panes, workspaces, layout, io, settings).
    for name, attr in vars(dash).items():
        if name.startswith("_"):
            continue
        lines.append(f"dash.{name} — {_first_doc_line(attr)}")
        for mname, mdoc in _public_methods(attr):
            suffix = f"  — {mdoc}" if mdoc else ""
            lines.append(f"    .{mname}(){suffix}")
        lines.append("")
    # Top-level methods (context, call, help).
    for mname, mdoc in _public_methods(dash):
        suffix = f"  — {mdoc}" if mdoc else ""
        lines.append(f"dash.{mname}(){suffix}")
    return "\n".join(lines)


def build_namespace(call: RelayCall) -> dict[str, Any]:
    """The starting globals for a REPL session: `dash`, with any backend-plugin
    facades attached (so `dash.<plugin>` and `dash.help()` pick them up)."""
    dash = Dash(call)
    from backend.sdk.registry import registry as _plugins

    for name, factory in _plugins.dash_facades.items():
        if hasattr(dash, name):
            continue  # never let a plugin shadow a core facade
        try:
            setattr(dash, name, factory())
        except Exception:  # noqa: BLE001 — a bad facade shouldn't break the REPL
            pass
    return {"dash": dash}


__all__ = ["Dash", "RelayCall", "build_namespace", "render_help"]
