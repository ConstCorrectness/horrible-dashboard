---
name: new-module
description: Conventions for adding a new feature module (panel, command, keybinding) to horrible-dashboard. Use before building any new feature — chat, dashboard widgets, notes, terminal, file explorer, or anything user-facing.
---

# Adding a feature module

Every feature is an internal module wired through the central registry in
`packages/core`. There is no public plugin API yet; these conventions are what we
will later harden into one, so don't bypass them "just this once".

## What a module is

A module is one directory that declares everything it contributes in a single
manifest object:

- **Commands** — named actions invokable from the command palette (`id`, `title`,
  `run()`). Every user-facing capability must be a command first; UI buttons call
  commands, never the other way around.
- **Panels** — React components the docking layout can host (`id`, `title`,
  `component`, default placement).
- **Keybindings** — default bindings that map keys to command ids. Never hardcode
  key handlers inside components.
- **Backend routes** (optional) — a FastAPI router in `backend/modules/<name>/`
  mounted under `/api/<name>`, with pydantic models for every request/response.

## Layout for a module named `notes`

```
packages/core/src/modules/notes/   # or its own package if it grows large
  index.ts          # exports the module manifest (the ONLY public surface)
  commands.ts
  panels/
backend/modules/notes/
  __init__.py       # exposes `router`
  models.py         # pydantic models
```

## Rules

1. Modules never import another module's internals — only its manifest/public
   exports or `packages/core` services. If two modules need to talk, they invoke
   each other's commands or share a core service.
2. Works in both layouts: no `window.__TAURI__` checks inside feature code — use
   the platform capability service in `packages/core` and degrade gracefully in
   the browser.
3. Register the module in the central registry list; nothing should be reachable
   only via a hardcoded route or import elsewhere.
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
