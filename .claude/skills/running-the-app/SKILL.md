---
name: running-the-app
description: How to start, run, and verify horrible-dashboard — backend API, browser layout dev server, and Tauri desktop layout. Use when asked to run, start, demo, screenshot, or verify the app.
---

# Running horrible-dashboard

Browser and desktop layouts share one frontend; the desktop layout is the same UI
inside a Tauri window. **One command brings up a full stack for each:**

- **Browser:** `pnpm dev` — starts the FastAPI backend _and_ the Vite UI together
  (via `scripts/dev.mjs`) on http://localhost:5173.
- **Desktop:** `pnpm dev:desktop` — `tauri dev`; the Tauri shell supervises the
  backend itself (`apps/desktop/src-tauri/src/backend.rs`), reusing one already
  running on :8000.

Add `pnpm dev:lan` to expose the browser stack on `0.0.0.0` for peer-fabric
collaboration. `dev:desktop` already binds `0.0.0.0`; `--host 127.0.0.1` opts out.
`pnpm dev:web` runs only the frontend (assumes a backend is already up).

## Backend alone (Python / FastAPI)

`pnpm dev` already starts this; run it standalone only when iterating on the backend:

```
uv run uvicorn backend.app:app --reload --reload-dir backend --reload-exclude "logs/*" --port 8000
```

- Run from the repo root. Health check: `GET http://localhost:8000/api/health`.
- **Don't drop `--reload-dir`/`--reload-exclude`.** A bare `uvicorn --reload` watches
  the whole repo root, including `logs/backend.log` — every log write then triggers a
  restart, which writes another log line, which triggers another restart.
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
pnpm dev            # from repo root; starts backend + UI on http://localhost:5173
```

`pnpm dev` runs the backend and frontend together, so API panels work out of the
box. If you started the backend separately and only want the UI, use `pnpm dev:web`.

## Desktop layout (Tauri)

```
pnpm dev:desktop    # from repo root (runs `tauri dev`)
```

- The Tauri shell supervises the backend itself (`src-tauri/src/backend.rs`):
  it spawns uvicorn, or reuses one already running on :8000. No separate backend
  start needed.
- Binds `0.0.0.0` by default (`scripts/dev-desktop.mjs` sets `HORRIBLE_DEV_HOST`),
  so a paired phone can reach it. Pass `--host 127.0.0.1` for local only.
- First build compiles Rust and takes several minutes — don't kill it for being slow.

## Verifying a change

1. `pnpm dev` (backend + UI together).
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
