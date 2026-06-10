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
loading the same dev server). The shared `/ws` socket is consumed via a single
multiplexed client (`packages/core/src/ws.ts`, channel subscriptions) — its first
user is the observability `telemetry` channel (see
[../modules/observability.md](../modules/observability.md)).

The shell has two top-level **views** (`registry.openView`):

- **`home`** — the default on open: a minimal centered surface (3D
  dashboard-friend avatar, greeting, ask bar streaming from the local model) that
  hosts agent onboarding until a local model is configured (see
  `docs/modules/agent-chat.md`). Modeled on chat-first launchers, deliberately
  free of workspace chrome. The avatar (`packages/ui/src/Avatar3D.tsx`) is a
  rigged glTF character (`/my-avatar.glb`) loaded with three's `GLTFLoader`,
  with pointer-tracking. It expresses the agent's **emotional mood** as a looping
  animation: the `moods` prop maps mood names to animation clips
  (`DEFAULT_AVATAR_MOODS` — `happy → /dancing.glb`, `flair → /flair.glb`,
  `error → /falling-over.glb`, all in `apps/web/public/`) and the `mood` prop
  selects the active one, cross-fading on change. Adding a mood is one `.glb`
  plus one line in the map. Orbiting the avatar is **Dashy**, the dashboard
  mascot — a cute glowing orb with eyes that face the viewer, whose glow doubles
  as the agent status light. three is dynamically imported so the workspace view
  never loads it.
- **`workspace`** — a **dockable window manager**: module panels open as windows
  that can be tabbed, split, resized, and floated; the layout persists. Built on
  dockview, wrapped so the registry stays the public API. See
  [windowing.md](windowing.md) for the model, panel types vs instances, and
  persistence.

A persistent **icon rail** (logo/home on top, panel icons, command palette at the
bottom) is the only chrome shared by both views. Opening a panel from anywhere
switches to the workspace view. The Workspace stays mounted across view switches
so its layout survives a trip home.

Branding: source art lives in `assets/` (`logo.svg`, `banner.svg`) —
`pnpm build:icons` regenerates the web favicon (`apps/web/public/favicon.ico`,
16/32/48), the SVG favicon copy, and the Tauri app icons (full size set) from
`assets/logo.svg`. The shell header shows the logo; `assets/banner.svg` is the
README banner.

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

| Capability             | Browser                            | Desktop (Tauri)                  |
| ---------------------- | ---------------------------------- | -------------------------------- |
| `fs.nativeDialogs`     | no (backend workspace roots only)  | yes (native open/save dialogs)   |
| `shell.revealInOS`     | no                                 | yes                              |
| `notifications.system` | Web Notifications (tab-scoped)     | OS notifications                 |
| `window.multi`         | no (single tab, pop-out = new tab) | yes (panel pop-out to OS window) |
| `shortcuts.global`     | no                                 | yes (summon window, quick chat)  |
| `tray`                 | no                                 | yes                              |

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
