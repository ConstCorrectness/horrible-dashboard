# horrible-dashboard

A unified one-stop app for everything — "emacs for the agentic era". One web UI serves
two layouts: a browser app and a cross-platform desktop app (Tauri shell wrapping the
same frontend).

## Current state

Scaffolded and verified: pnpm monorepo (apps/web, apps/desktop, packages/core,
packages/ui), FastAPI backend, the **dashboard module** end to end, and the
**agent module's first slice** — a Gemini-style `home` view (default on open) with
3D avatar, local-model onboarding via Ollama (default `gemma4:e2b`), and a
streaming ask bar. The shell has `home`/`workspace` views with an icon rail;
dockable workspace is still the target. An experimental Electron shell lives on
the `electron-shell` branch. Remaining modules (editor, terminal, files, full chat
cockpit) are unimplemented — see docs/ for their designs.

## Stack (decided, do not re-litigate)

- **Frontend:** React + TypeScript, Vite, pnpm workspaces
- **Desktop shell:** Tauri (Rust) — wraps the same frontend as the browser build
- **Backend:** Python 3.12 + FastAPI, managed with `uv`. The backend is the app's
  brain: agents, data, MCP integrations, websockets to the UI.
- **Extensibility:** built-in modules first. Every feature (chat, dashboard, notes,
  terminal, files) is an internal module registered through a central registry
  (commands, panels, keybindings). No public plugin API yet — extract one later once
  the module patterns stabilize.

## Target layout

```
apps/
  web/            # Vite + React entry for the browser layout
  desktop/        # Tauri app (src-tauri/) reusing the same frontend
packages/
  core/           # TS core: module registry, command palette, keybindings, API client
  ui/             # shared React components and panel/docking system
backend/          # FastAPI app: agents, modules' server side, websockets
```

First modules, in priority order: agent chat cockpit, dashboard/widgets, notes/editor
buffers, terminal + file explorer.

## Commands

- `uv run pytest` — backend tests
- `uv run uvicorn backend.app:app --reload --port 8000` — backend dev server
- `uv run ruff format .` and `uv run ruff check --fix .` — Python format/lint
- `uv add <pkg>` / `uv add --dev <pkg>` — Python dependencies (never pip)
- `pnpm dev` — browser layout dev server (port 5173, proxies /api and /ws to 8000)
- `pnpm dev` in `apps/desktop` — desktop layout (Tauri; starts the web dev server)
- `pnpm typecheck`, `pnpm lint` — whole workspace, from the root
- `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml` — Rust check

## Conventions

- TypeScript strict mode; no `any` without a comment explaining why.
- Pydantic models for every API boundary; the backend is the source of truth for
  shared types.
- New features go through the module registry — a module declares its commands,
  panels, and keybindings in one place; nothing reaches into another module's
  internals. See `.claude/skills/new-module/SKILL.md` before adding a feature.
- Browser and desktop layouts share one codebase; never fork a component per
  platform — branch on a platform capability check instead.
- Formatting/linting is automatic: a PostToolUse hook runs ruff (Python), prettier +
  eslint (TS/JS), and rustfmt (Rust) on every file you edit. Don't hand-format.

## Documentation (docs/)

`docs/` documents the layout shell and every module — see
[docs/README.md](docs/README.md) for the index and the full sync policy. The short
version: adding or changing a module, panel, command, capability, backend route, or
layout-shell behavior must update the matching `docs/` page **in the same change**
(new module → new `docs/modules/<name>.md`). A Stop hook flags code changes under
`apps/`, `packages/`, or `backend/` that don't touch `docs/`; pure refactors are
exempt — just say so.
