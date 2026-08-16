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
    """Open, close, and inspect open panes (role-routed: documents tab into center
    areas, widgets take their own area, tools go to their dock)."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def available(self) -> Any:
        """Every view that can be opened, with id, title, role (document/widget/tool),
        default dock (tools), and the region views it hosts."""
        return self._call("list_available_panes", {})

    def open_list(self) -> Any:
        """Pane instances currently open in the active workspace (view id, instanceId,
        role, location: center area / dock / floating)."""
        return self._call("list_open_panes", {})

    def open(self, view_id: str) -> Any:
        """Open a view by its view ID (from `available()`), routed by its role."""
        return self._call("open_pane", {"id": view_id})

    def close(self, instance_id: str) -> Any:
        """Close an open pane instance by its instance ID (from `open_list()`)."""
        return self._call("close_pane", {"id": instance_id})

    def focus(self, instance_id: str) -> Any:
        """Bring a pane forward (tab / dock slot / floating card)."""
        return self._call("focus_pane", {"instanceId": instance_id})


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
    """Arrange the frame — split/join/resize center areas, move panes, toggle
    regions and docks, fullscreen, float — the same verbs the agent's layout
    control uses. Pane targets are **instance ids** (from `dash.panes.open_list()`);
    area ids come from `describe()`."""

    def __init__(self, call: RelayCall) -> None:
        self._call = call

    def describe(self) -> Any:
        """The whole frame: center split tree (areas, tabs, regions), docks,
        floating panes, fullscreen/focused area."""
        return self._call("get_layout", {})

    def split(
        self, instance_id: str, direction: str, view_id: str | None = None
    ) -> Any:
        """Split the area holding a pane (`left`/`right`/`above`/`below`, or the
        `vertical`/`horizontal` aliases); `view_id` to open a different view in the
        new area, else the pane's own view is duplicated."""
        args: dict[str, Any] = {"instanceId": instance_id, "direction": direction}
        if view_id is not None:
            args["viewId"] = view_id
        return self._call("split_area", args)

    def join(self, instance_id: str, direction: str) -> Any:
        """Absorb the neighboring area (`left`/`right`/`up`/`down`) into this
        pane's area; document tabs are adopted when both sides hold documents."""
        return self._call(
            "join_area", {"instanceId": instance_id, "direction": direction}
        )

    def move(
        self,
        instance_id: str,
        area_id: str | None = None,
        direction: str | None = None,
        edge: str | None = None,
    ) -> Any:
        """Move a center pane into `area_id` (from `describe()`), toward
        `direction` (`left`/`right`/`up`/`down`), or onto an area's `edge`
        (`left`/`right`/`above`/`below`), which splits it and lands the pane in
        the new half."""
        args: dict[str, Any] = {"instanceId": instance_id}
        if area_id is not None:
            args["areaId"] = area_id
        if direction is not None:
            args["direction"] = direction
        if edge is not None:
            args["edge"] = edge
        return self._call("move_pane", args)

    def resize(
        self, instance_id: str, width: int | None = None, height: int | None = None
    ) -> Any:
        """Resize the area holding a pane (pixels)."""
        return self._call(
            "resize_area", {"instanceId": instance_id, "width": width, "height": height}
        )

    def fullscreen(self, instance_id: str | None = None, on: bool = True) -> Any:
        """Expand a pane's area to fill the frame, or restore with `on=False`."""
        args: dict[str, Any] = {"on": on}
        if instance_id is not None:
            args["instanceId"] = instance_id
        return self._call("fullscreen_area", args)

    def toggle_region(
        self, instance_id: str, position: str, open: bool | None = None
    ) -> Any:
        """Toggle a pane's region strip (`left`/`right`/`bottom`); `open` forces a state."""
        args: dict[str, Any] = {"instanceId": instance_id, "position": position}
        if open is not None:
            args["open"] = open
        return self._call("toggle_region", args)

    def set_region_view(self, instance_id: str, view_id: str) -> Any:
        """Show a specific region view on its host pane (opens the strip)."""
        return self._call(
            "set_region_view", {"instanceId": instance_id, "viewId": view_id}
        )

    def open_tool(self, view_id: str, dock: str | None = None) -> Any:
        """Open (or focus) a role:'tool' view in a dock (defaults to its own side)."""
        args: dict[str, Any] = {"id": view_id}
        if dock is not None:
            args["dock"] = dock
        return self._call("open_tool_in_dock", args)

    def toggle_dock(self, dock: str, visible: bool | None = None) -> Any:
        """Show/hide a dock (`left`/`right`/`bottom`); `visible` forces a state."""
        args: dict[str, Any] = {"dock": dock}
        if visible is not None:
            args["visible"] = visible
        return self._call("toggle_dock", args)

    def window(
        self,
        instance_id: str,
        snap: str | None = None,
        rect: dict[str, float] | None = None,
    ) -> Any:
        """Pop a pane out into a free-floating desktop window.

        `snap` places it in a screen region (`left`/`right`/`top`/`bottom`, a
        corner, or `max`); `rect` gives exact pixels. Windows work on a tiling
        desktop too — there they are the escape hatch for a pane that should not
        participate in the tiling.
        """
        args: dict[str, Any] = {"instanceId": instance_id}
        if snap is not None:
            args["snap"] = snap
        if rect is not None:
            args["rect"] = rect
        return self._call("open_window", args)

    def dock(self, instance_id: str) -> Any:
        """Put a windowed pane back into the tiling frame."""
        return self._call("dock_window", {"instanceId": instance_id})

    def window_state(
        self,
        instance_id: str,
        state: str,
        snap: str | None = None,
        workspace_id: str | None = None,
    ) -> Any:
        """`minimize`, `maximize`, `restore`, `snap` or `move_to_desktop`.

        A minimized window keeps running — it is hidden, not closed.
        """
        args: dict[str, Any] = {"instanceId": instance_id, "state": state}
        if snap is not None:
            args["snap"] = snap
        if workspace_id is not None:
            args["workspaceId"] = workspace_id
        return self._call("window_state", args)

    def arrange(self, style: str = "grid") -> Any:
        """Lay every open window out: `grid`, `cascade`, `columns` or `rows`."""
        return self._call("arrange_windows", {"style": style})

    def mode(self, mode: str) -> Any:
        """Switch this desktop between `tiling` and `floating`."""
        return self._call("desktop.set_mode", {"mode": mode})

    def backdrop(self, backdrop_id: str, **params: Any) -> Any:
        """Set the desktop backdrop. Ids are in `describe()["desktop"]["backdrops"]`."""
        args: dict[str, Any] = {"id": backdrop_id}
        if params:
            args["params"] = params
        return self._call("desktop.set_backdrop", args)

    def theme(self, theme_id: str) -> Any:
        """Switch the app theme. Ids are in `describe()["desktop"]["themes"]`."""
        return self._call("desktop.set_theme", {"id": theme_id})


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


class _Code:
    """Read the code index and drive the shared **code locus** (the 'what code am I
    looking at' cursor: path + optional line/symbol) from Python. Backend-local: no
    browser round-trip. Setting the locus makes every attached browser follow it."""

    def locus(self) -> dict[str, Any]:
        """The current code locus (path + optional range/symbol), or `{}` if none."""
        from backend.modules.code import current_locus

        return current_locus()

    def set_locus(
        self, path: str, line: int | None = None, symbol: str | None = None
    ) -> dict[str, Any]:
        """Point the locus at `path` (optionally a 1-based `line`/`symbol`); browsers
        open and scroll to it."""
        locus: dict[str, Any] = {"path": path}
        if line is not None:
            pos = {"line": line, "column": 1}
            locus["range"] = {"start": pos, "end": pos}
        if symbol is not None:
            locus["symbol"] = symbol
        from backend.modules.code import set_locus_from_backend

        return set_locus_from_backend(locus)

    def symbols(self, path: str) -> list[dict[str, Any]]:
        """The definitions (outline) in a file — functions, classes, methods, …."""
        from pathlib import Path

        from backend.modules.code import code_index

        return [s.model_dump() for s in code_index.document_symbols(Path(path))]

    def find(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fuzzy symbol search across the workspace roots (exact-name / structural)."""
        from backend.modules.code import code_index
        from backend.modules.files.routes import _roots

        return [h.model_dump() for h in code_index.find_symbols(query, _roots(), limit)]

    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Semantic search: find definitions by meaning (embeddings). Returns
        `{building: True}` with no results until the index is built — run
        `dash.code.reindex()` first if so."""
        import asyncio

        from backend.modules.code import semantic_index
        from backend.modules.files.routes import _roots

        return asyncio.run(semantic_index.search(query, _roots(), limit))

    def reindex(self) -> dict[str, Any]:
        """Rebuild the semantic index over the workspace roots (blocks until done)."""
        import asyncio

        from backend.modules.code import semantic_index
        from backend.modules.files.routes import _roots

        return asyncio.run(semantic_index.reindex(_roots()))


class _Git:
    """Read git provenance and author commits from Python. Backend-local. `commit`
    stamps the active chat session as a trailer, so `blame` can attribute lines back to
    the conversation that wrote them."""

    def _hint(self) -> Any:
        from pathlib import Path

        from backend.modules.files.routes import _roots

        roots = _roots()
        return roots[0] if roots else Path(".")

    def blame(self, path: str) -> dict[str, Any]:
        """Per-line authorship for a file, each line tagged with the session that wrote
        its commit (if any)."""
        from pathlib import Path

        from backend.modules.git import service

        return service.blame(Path(path)).model_dump()

    def log(self, limit: int = 20) -> dict[str, Any]:
        """Recent commits; a `session_id` marks a commit agent-authored."""
        from backend.modules.git import service

        return service.log(self._hint(), limit).model_dump()

    def commit(self, message: str, paths: list[str] | None = None) -> dict[str, Any]:
        """Stage + commit, stamping the active chat session as provenance trailers."""
        from backend.modules.git import service

        return service.commit(self._hint(), message, paths).model_dump()


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
        self.code = _Code()
        self.git = _Git()

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
