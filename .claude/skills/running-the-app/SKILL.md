---
name: running-the-app
description: How to start, run, and verify horrible-dashboard — backend API, browser layout dev server, and Electron desktop layout. Use when asked to run, start, demo, screenshot, or verify the app.
---

# Running horrible-dashboard

Browser and desktop layouts share one frontend; the desktop layout is the same UI
inside an Electron window. **One command brings up a full stack for each:**

- **Browser:** `pnpm dev` — starts the FastAPI backend _and_ the Vite UI together
  (via `scripts/dev.mjs`) on http://localhost:5173.
- **Desktop:** `pnpm dev:desktop` — Vite plus the Electron shell; the shell
  supervises the backend itself (`apps/desktop/src/backend.ts`), reusing one
  already running on :8000.

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

## Desktop layout (Electron)

```
pnpm dev:desktop    # from repo root: Vite + Electron (apps/desktop)
```

- The shell supervises the backend itself (`apps/desktop/src/backend.ts`):
  it spawns uvicorn, or reuses one already running on :8000. No separate backend
  start needed. Closing the window stops the backend and Vite.
- Binds `0.0.0.0` by default (`scripts/dev-desktop.mjs` sets `HORRIBLE_DEV_HOST`),
  so a paired phone can reach it. Pass `--host 127.0.0.1` for local only.
- F12 / Ctrl+Shift+I opens DevTools in development.
- In a headless container: `xvfb-run -a` in front, and Electron needs
  `--no-sandbox` (unprivileged user namespaces are usually off).

### The packaged app, end to end

```
pnpm build && pnpm --filter @horrible/desktop run package   # apps/desktop/release/<os>-unpacked
node scripts/desktop-smoke.mjs        # Linux headless: xvfb-run -a … --no-wm
node scripts/desktop-bench.mjs --exe <unpacked binary> --label electron
```

`desktop-smoke.mjs` drives the real binary through Playwright (backend, CORS and
`/ws`, every shell command family, guards, shutdown) and writes
`desktop-smoke-results/`. The Desktop workflow runs both on Windows and Linux.

## Verifying a change

1. `pnpm dev` (backend + UI together).
2. Use browser tooling (preview/screenshot) against `http://localhost:5173`.
3. Only launch the desktop shell when the change touches desktop-specific behavior (window
   chrome, tray, native menus, platform capability branches) — otherwise the
   browser layout is sufficient and much faster.

## Other checks

- `pnpm typecheck` and `pnpm lint` from the root cover all TS packages.
- `uv run pytest` covers the backend (`backend/tests/`).
- `pnpm --filter @horrible/desktop test` covers the shell's own logic.

## Keep this file honest

As pieces become real (ports, entry points, scripts), update this skill in the
same change.
