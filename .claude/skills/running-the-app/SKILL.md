---
name: running-the-app
description: How to start, run, and verify horrible-dashboard — backend API, browser layout dev server, and Tauri desktop layout. Use when asked to run, start, demo, screenshot, or verify the app.
---

# Running horrible-dashboard

The app has three runnable pieces. Browser and desktop layouts share one frontend;
the desktop layout is the same UI inside a Tauri window.

## Backend (Python / FastAPI)

```
uv run uvicorn backend.app:app --reload --port 8000
```

- Run from the repo root. Health check: `GET http://localhost:8000/health`.
- Until the FastAPI app is scaffolded, only `uv run python main.py` exists.

## Browser layout (web)

```
pnpm install        # first time only
pnpm dev            # from repo root; runs apps/web on http://localhost:5173
```

The frontend expects the backend on port 8000 — start it first or API panels will
show connection errors (that is not a frontend bug).

## Desktop layout (Tauri)

```
pnpm tauri dev      # from apps/desktop
```

- First build compiles Rust and takes several minutes — don't kill it for being slow.
- Requires the same backend running; the Tauri shell does not bundle it in dev.

## Verifying a change

1. Start backend, then `pnpm dev`.
2. Use browser tooling (preview/screenshot) against `http://localhost:5173`.
3. Only launch Tauri when the change touches desktop-specific behavior (window
   chrome, tray, native menus, platform capability branches) — otherwise the
   browser layout is sufficient and much faster.

## Keep this file honest

The repo is pre-scaffold. As pieces become real (ports, entry points, scripts),
update this skill in the same change.
