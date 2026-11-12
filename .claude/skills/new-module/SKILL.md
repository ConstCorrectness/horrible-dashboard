---
name: new-module
description: Conventions for adding a new feature module (panel, command, keybinding) to horrible-dashboard. Use before building any new feature — chat, dashboard widgets, notes, terminal, file explorer, or anything user-facing.
---

# Adding a feature module

A new feature is a **module package** under `modules/<id>/`: it can be enabled,
disabled, installed and uninstalled while the app runs, and its directory can later
move to its own repo as a git submodule. Read
[docs/architecture/modules.mdx](../../docs/architecture/modules.mdx) first;
`modules/karaoke` is the reference package. (Older features are still in-tree under
`packages/core/src/modules` and migrate in batches; follow the same manifest rules
there until they move.)

## What a module is

A module is one directory that declares everything it contributes in a single
manifest object:

- **Commands** — named actions invokable from the command palette (`id`, `title`,
  `run()`). Every user-facing capability must be a command first; UI buttons call
  commands, never the other way around.
- **Panels** — React components the desktop hosts as windows (`id`,
  `title`, `component`, `role` — `document`, `tool` or `widget`). Set `singleton: true` if only one
  window of the panel should exist (opening again focuses it, e.g. the
  dashboard); omit it for panels you open many of (terminals, editor buffers).
  Declare the component with `lazyPane(() => import('./panels/Foo'), 'Foo')`
  (`packages/core/src/lazy-pane.ts`) rather than a static import, so the pane's
  code — and any heavy library it pulls in — loads when the pane first opens, not at
  boot. Don't also import or re-export that file from `index.ts`, or it stays eager.
  Panels render registry-side — they don't import the windowing engine. See
  [docs/architecture/windowing.md](../../docs/architecture/windowing.md);
  `scratch` is the reference panel.
- **Keybindings** — default bindings that map keys to command ids. Never hardcode
  key handlers inside components.
- **Backend routes** (optional) — a FastAPI router (`prefix="/<name>"`) added with
  `host.add_router(router)` in the module's `setup`; it is served under
  `/api/<name>`. Pydantic models for every request/response.

## Layout for a module named `notes`

```
modules/notes/
  horrible-module.json   # id, web.entry, py, toolGroups, panes, requires/provides
  README.md
  web/                   # @horrible-module/notes: package.json, tsconfig.json
    src/index.ts         # export const module = defineModule({...}) from @horrible/kernel
    src/panels/
  py/                    # horrible-mod-notes: pyproject.toml (entry point horrible.modules)
    src/horrible_mod_notes/__init__.py   # MODULE = NotesModule(); setup(host) adds router + tools
    tests/conftest.py    # self-contained: py/src on sys.path, tmp data/config/cache dirs
```

No edit to `main.tsx`, `packages/core/src/index.ts` or `backend/app.py`: the Vite
plugin and `ModuleManager` discover the package. Run `pnpm install` once so the web
half joins the workspace.

## Rules

1. A package imports only `@horrible/kernel`, `@horribledashboard/sdk`,
   `backend.kernel`, `backend.sdk` and its own files (ESLint + the
   `pnpm check:boundaries` ratchet enforce it). Anything another module owns is a
   typed service (`packages/sdk/src/contracts.ts`, `ctx.provide` / `getService`),
   and the user handles the service being absent. Missing a kernel export? Add it
   to `packages/core/src/kernel-surface.ts` and `packages/kernel/src/index.ts`.
   Never reach into core.
2. Works in both layouts: no `window.__TAURI__` checks inside feature code — use
   the platform capability service in `packages/core` and degrade gracefully in
   the browser.
3. Agent tools are named `<group>.<tool>` for a group declared in `toolGroups` (load
   fails otherwise) and registered with `host.add_agent_tool`. Long-running work
   goes in `host.background(...)` so a disable cancels it; module globals survive
   a disable, so `setup` must be safe to run again.
4. Backend side ships with pytest coverage for its routes; frontend commands get
   at least a registration smoke test.
5. Every module has a docs page: create `docs/modules/<name>.md` in the same
   change, following the existing pages' template (contributions to the layout
   shell, backend surface, browser vs desktop table). Changing a module's
   panels/commands/capabilities later means updating its page — a Stop hook
   checks for this.

## If the registry doesn't exist yet

The repo may still be pre-scaffold. If `packages/core` has no module registry yet,
build the minimal registry first (manifest type + register/list + command palette
lookup), then add your feature as its first consumer — do not ship a feature as a
one-off and "extract the registry later".
