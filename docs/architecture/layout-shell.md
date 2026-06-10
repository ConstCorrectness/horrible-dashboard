# Layout shell: one frontend, two layouts

The browser app and the desktop app render the **same React frontend**. There is
no per-platform fork of the UI. The differences between the two are confined to
(a) the app entry that boots the shell and (b) the platform capabilities exposed
to modules.

```
            ┌────────────────────────────────────────────┐
            │              layout shell (packages/ui)     │
            │  workspace (dock/split/tabs) · command       │
            │  palette · status bar · keybinding service   │
            └───────▲────────────────────────────▲────────┘
                    │ boots                      │ boots
        apps/web (browser)            apps/desktop (Tauri)
        plain Vite entry              Rust shell + same frontend
                    │                            │
                    └──────────┬─────────────────┘
                               │ HTTP + WebSocket
                        backend/ (FastAPI)
                agents · PTYs · files · notes · widgets data
```

## Implementation status

Implemented: module registry, command palette (Ctrl+K), keybinding service
(`mod+` prefix), capability service, and both entries (`apps/web` on port 5173
proxying `/api` and `/ws` to the backend on port 8000; `apps/desktop` Tauri shell
loading the same dev server). The workspace is currently a **sidebar panel
switcher**, not yet the dockable split/tab system described below — that remains
the target. The backend exposes the shared `/ws` socket but the frontend does
not consume it yet.

Branding: `logo.svg` at the repo root is the single source for all icons —
`pnpm build:icons` regenerates the web favicon (`apps/web/public/favicon.ico`),
the SVG favicon copy, and the Tauri app icons from it. The shell header shows
the logo; `banner.svg` is the README banner.

## The workspace

The shell owns a dockable workspace: panels arranged in split panes and tab
groups, persisted per layout profile. Modules never render themselves into the
DOM directly — they **register panels** with the module registry
(`packages/core`), and the workspace decides where panels live (default
placement comes from the panel declaration; the user can rearrange freely).

Everything user-facing routes through two shell services:

- **Command palette** — every module capability is a registered command
  (`module.verb` ids, e.g. `terminal.new`, `chat.focusInput`). Panels' buttons
  invoke commands; the palette and keybindings invoke the same commands. This is
  what keeps both layouts behaviorally identical.
- **Keybinding service** — maps keys to command ids. Modules declare defaults;
  the shell resolves conflicts and (on desktop) registers any global shortcuts.

## Platform capability service

Modules must never check `window.__TAURI__` or user-agent sniff. `packages/core`
exposes a capability service; feature code branches on capabilities and degrades
gracefully:

| Capability               | Browser                          | Desktop (Tauri)                  |
| ------------------------ | -------------------------------- | -------------------------------- |
| `fs.nativeDialogs`       | no (backend workspace roots only)| yes (native open/save dialogs)   |
| `shell.revealInOS`       | no                               | yes                              |
| `notifications.system`   | Web Notifications (tab-scoped)   | OS notifications                 |
| `window.multi`           | no (single tab, pop-out = new tab)| yes (panel pop-out to OS window) |
| `shortcuts.global`       | no                               | yes (summon window, quick chat)  |
| `tray`                   | no                               | yes                              |

## Backend connection

Both layouts talk to the same FastAPI backend over HTTP + WebSocket.

- **Desktop:** the Tauri app targets `localhost` (backend started alongside it in
  dev; bundling strategy TBD for release).
- **Browser:** the web app targets a configured backend URL — `localhost` in dev,
  potentially a remote host later. Anything that executes on the backend
  (terminals, file access, agents) therefore runs **where the backend runs**, not
  where the browser runs. Module docs call this out where it matters.

The shell owns a single multiplexed WebSocket connection with reconnect/backoff;
modules subscribe to channels on it rather than opening their own sockets.

## What belongs in each entry

- `apps/web`: boot the shell, read backend URL config, register the
  browser capability set. Nothing feature-specific.
- `apps/desktop`: same boot, plus Tauri-only wiring — window chrome, tray, global
  shortcuts, multi-window pop-out, native menu that dispatches to the same
  command registry. Rust code in `src-tauri/` stays a thin shell; app logic
  belongs in the backend or `packages/`.
