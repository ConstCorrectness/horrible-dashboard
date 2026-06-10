# Electron desktop shell (experiment)

> **Status: experiment.** This variant lives on the `electron-shell` branch as a
> parallel exploration to the Tauri shell (`apps/desktop`) being built on `main`. The
> decided stack remains Tauri (see [CLAUDE.md](../../CLAUDE.md)); this exists to
> compare the two shells on equal footing. Code: `apps/desktop-electron/`.

## What it is

A thin Electron wrapper around the **same web frontend** as the browser layout. It
scaffolds no UI of its own: the main process opens a 1280x800 BrowserWindow titled
`horrible-dashboard (electron)` and points it at the Vite dev server that serves
`apps/web`. Browser and desktop layouts share one codebase; the shell only differs in
what platform capabilities it declares.

## Dev URL contract

- The window loads `process.env.ELECTRON_DEV_URL ?? "http://localhost:5173"` — the
  default address of the `apps/web` Vite dev server (`pnpm dev`).
- If that URL fails to load (dev server not running), the shell falls back to a bundled
  `placeholder.html` that tells you to start the web dev server (`pnpm dev`) and
  reload. The placeholder's Reload button retries the dev URL; if it is still down, the
  shell lands back on the placeholder.
- The FastAPI backend (http://localhost:8000) is reached by the frontend itself, not by
  the shell — same as in the browser layout.

## Preload capability bridge

The frontend never forks per platform; it branches on a capability check. The preload
script (`src/preload.ts`) exposes, via `contextBridge` (with `contextIsolation: true`,
`nodeIntegration: false`, `sandbox: true`):

```ts
window.horriblePlatform = {
  shell: "electron",
  capabilities: [
    "fs.nativeDialogs",
    "shell.revealInOS",
    "notifications.system",
    "window.multi",
    "shortcuts.global",
    "tray",
  ],
};
```

These are the capability names from
[architecture/layout-shell.md](layout-shell.md). For now this is a declaration list
only — none of the capabilities are implemented; the bridge exists so the shared
frontend can already branch on `shell` / `capabilities` identically across shells.

## Build and run

From `apps/desktop-electron/`:

- `pnpm build` — `tsc` compiles `src/` to `dist/main.js` + `dist/preload.js`
- `pnpm dev` — build, then `electron .`
- `pnpm typecheck` — `tsc --noEmit`

The package is self-contained (own `devDependencies`: `electron`, `typescript`,
`@types/node`) and does not depend on any workspace package.

## Comparison vs the Tauri shell

| Aspect      | Electron                                          | Tauri                                                        |
| ----------- | ------------------------------------------------- | ------------------------------------------------------------ |
| Binary size | Large (~100+ MB; bundles Chromium + Node)         | Small (~few MB; uses the OS webview)                          |
| Runtime     | Chromium + Node.js, identical on every platform   | Native webview (WebView2/WKWebView/WebKitGTK), Rust backend   |
| Ecosystem   | Mature, huge npm ecosystem, JS/TS end to end      | Younger but growing; native side is Rust (crates, plugins)    |

Electron buys rendering consistency and a TS-only toolchain at the cost of footprint;
Tauri buys small binaries and native performance at the cost of per-platform webview
quirks and a Rust toolchain. This experiment exists to measure that trade-off against
horrible-dashboard's real frontend before committing further.
