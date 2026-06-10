# Windowing: the dockable workspace

The `workspace` view (see [layout-shell.md](layout-shell.md)) is a dockable window
manager: module panels open as **windows** that can be tabbed, split, resized,
and floated. This replaced the earlier single-panel switcher.

## Decisions

- **Engine: [dockview](https://dockview.dev), wrapped.** dockview (MIT, React +
  TS) provides tabs, resizable splits, floating groups, and layout
  serialization. It lives behind `packages/ui/src/Workspace.tsx` so the **module
  registry stays the public API** — modules never import dockview. We could swap
  the engine without touching modules.
- **Two levels, not one.** Dockable windows host module _panels_ (dashboard,
  scratch, and later editor/terminal/chat). Smaller dashboard _widgets_ remain a
  grid _inside_ the dashboard panel (see [../modules/dashboard.md](../modules/dashboard.md)) —
  docking and a widget board are deliberately separate concepts.

## Model

A workspace layout is a serializable tree owned by the engine:

```
Workspace
├── Split (row/column, resizable)
│   ├── TabGroup ── [panel instance, panel instance, …]
│   └── Split (…)
└── FloatingLayer ── [FloatingWindow → panel instance, …]
```

- **Split** — a resizable row/column of children (drag the divider).
- **TabGroup** — stacked panels sharing one rectangle (drag tabs to reorder, to
  another group, or out to split/float).
- **Panel instance** — a live registry panel rendered inside a window.
- **FloatingLayer** — windows not docked into the grid; move/resize freely.

## Panel _types_ vs _instances_

A `PanelDecl` in the registry is a panel **type**. The workspace creates
**instances** of it:

- `singleton: true` (e.g. `dashboard.home`) — opening again focuses the one
  existing window.
- omitted/false (e.g. `scratch.note`) — each open creates a new instance with a
  unique id (`scratch.note#2`, `#3`, …). This is how you get N terminals or N
  editor buffers.

`defaultPlacement` (`left|center|right|bottom`) is the hint for where a freshly
opened panel docks; once the user rearranges, the persisted tree wins.

## How panels reach the workspace

1. A rail button or command calls `registry.openPanel(panelId)`.
2. The shell switches to the `workspace` view and signals `Workspace` which panel
   to open (via a bumped `pendingOpen` nonce, so repeats re-fire).
3. `Workspace` renders every panel through one host component (`PanelHost`) that
   looks the panel up in the registry by `params.panelId` — so dockview's
   serialized layout only stores panel ids, and restore just re-resolves them.

## Persistence

`Workspace` serializes the dockview layout on change (debounced ~600ms) to
`PUT /api/workspace/layout`, and restores it on mount from
`GET /api/workspace/layout`. The backend
(`backend/modules/workspace/`) stores the layout **opaquely** — it never
interprets the engine's JSON shape (`SerializedLayout` in
`packages/core/src/workspace.ts` is `Record<string, unknown>`). No saved layout →
`Workspace` opens the dashboard as a sensible default.

## Browser vs desktop

The floating layer is where the `window.multi` capability will matter: in the
**browser** a floating window is an in-document pane (implemented today); on
**desktop (Tauri)** the same action can pop a panel out to a _real OS window_.
The seam is `Workspace` + the capability service; the OS-window path is not built
yet.

## Dev affordance

In dev builds (`import.meta.env.DEV`), `Workspace` exposes the live dockview API
as `window.__horribleWorkspace` for console/preview experimentation
(`addPanel`, `toJSON`, …). It is not set in production builds.

## Status

Implemented: splits, tab groups, floating windows, layout persistence/restore,
the singleton/instance distinction. `scratch.note` is the reference non-singleton
panel. Not yet: real OS windows on desktop, per-instance panel state (scratch
instances currently share one store), and richer panels from future modules.
