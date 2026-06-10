# horrible-dashboard documentation

Architecture and module documentation. These pages are the contract between the
code and anyone (human or agent) working on it — they must describe the codebase
as it is, not as it once was.

## Contents

- [architecture/layout-shell.md](architecture/layout-shell.md) — the shared
  frontend layout (workspace, panels, command palette) and how the browser and
  desktop (Tauri) versions differ.
- [architecture/windowing.md](architecture/windowing.md) — the dockable
  workspace: how panels open as tabbed/split/floating windows, panel
  types vs instances, and layout persistence.
- `modules/` — one page per feature module, each describing how it plugs into the
  layout shell and how it behaves in the browser vs the desktop app:
  - [modules/agent-chat.md](modules/agent-chat.md)
  - [modules/dashboard.md](modules/dashboard.md)
  - [modules/scratch.md](modules/scratch.md)
  - [modules/clubhouse.md](modules/clubhouse.md)
  - [modules/observability.md](modules/observability.md)
  - [modules/editor.md](modules/editor.md)
  - [modules/terminal.md](modules/terminal.md)
  - [modules/file-explorer.md](modules/file-explorer.md)

## Sync policy (enforced)

Docs live next to the code on purpose, and changes that affect them must update
them **in the same change**:

- New module → new `docs/modules/<name>.md` page following the existing template
  (purpose, panels/commands contributed, backend surface, browser vs desktop
  behavior).
- New panel, command, capability, or backend route on an existing module → update
  that module's page.
- Changes to the workspace/docking system, command palette, keybindings, platform
  capability service, or either app entry → update `architecture/layout-shell.md`.
- Renaming or removing any of the above → same rule, including deleting docs for
  removed features.

A Stop hook (`.claude/hooks/docs_check.py`) flags any change set that touches
`apps/`, `packages/`, or `backend/` without touching `docs/`, as a safety net.
Pure refactors with no behavioral or interface change don't need doc edits — say
so when the hook asks.

## Status

Scaffolded: the monorepo (apps/web, apps/desktop, packages/core, packages/ui,
backend/) exists and the **dashboard module** is implemented end to end. The
other module pages (agent-chat, editor, terminal, file-explorer) still document
design only — update each to match reality when it gets implemented. An
experimental Electron shell lives on the `electron-shell` branch
(`docs/architecture/electron-shell.md` there).
