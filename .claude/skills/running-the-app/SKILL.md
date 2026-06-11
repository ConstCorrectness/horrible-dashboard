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

- Run from the repo root. Health check: `GET http://localhost:8000/api/health`.
- **Windows OpenSSL gotcha (handled in code):** if `C:\msys64\mingw64\bin` (or
  Git's `mingw64\bin`) is on PATH, MinGW's applink-less `libcrypto` could be
  loaded for TLS and abort the worker — `OPENSSL_Uplink ... no OPENSSL_Applink`,
  surfacing as Vite proxy `ECONNRESET`/`ECONNREFUSED`. `backend/__init__.py` now
  strips MinGW dirs from the process PATH at import, so the bare command above
  just works regardless of your shell PATH. `scripts/dev-backend.ps1` does the
  same at the shell level and remains as a belt-and-suspenders option.

## Browser layout (web)

```
pnpm install        # first time only
pnpm dev            # from repo root; runs apps/web on http://localhost:5173
```

The frontend expects the backend on port 8000 — start it first or API panels will
show connection errors (that is not a frontend bug).

## Desktop layout (Tauri)

```
pnpm dev            # from apps/desktop (runs `tauri dev`, which also starts the web dev server)
```

- First build compiles Rust and takes several minutes — don't kill it for being slow.
- Requires the same backend running; the Tauri shell does not bundle it in dev.

## Verifying a change

1. Start backend, then `pnpm dev`.
2. Use browser tooling (preview/screenshot) against `http://localhost:5173`.
3. Only launch Tauri when the change touches desktop-specific behavior (window
   chrome, tray, native menus, platform capability branches) — otherwise the
   browser layout is sufficient and much faster.

## Other checks

- `pnpm typecheck` and `pnpm lint` from the root cover all TS packages.
- `uv run pytest` covers the backend (`backend/tests/`).
- Electron experiment: branch `electron-shell`, `apps/desktop-electron`,
  `pnpm dev` there after starting the web dev server.

## Keep this file honest

As pieces become real (ports, entry points, scripts), update this skill in the
same change.
