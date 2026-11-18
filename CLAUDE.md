# horrible-dashboard

A unified one-stop app for everything — "emacs for the agentic era". One web UI serves
two layouts: a browser app and a cross-platform desktop app (an Electron shell wrapping
the same frontend).

## Current state

Every feature is a module registered through the central registry; `docs/modules/`
has a page per module and [docs/README.mdx](docs/README.mdx) indexes them. The window
model is a **docked workbench**: a centre split tree of tab groups, three edge docks,
and on desktop (`window.multi`) pop-out OS windows. See
docs/architecture/windowing.mdx, which also says why the floating desktop it replaced
was removed. A backend agent orchestrator drives the UI over the shared `/ws` socket
(docs/modules/agent-chat.mdx). Public plugin SDKs exist for both sides
(docs/architecture/plugin-sdk.mdx, docs/architecture/backend-plugin-sdk.mdx).

(This file was emptied by an unrelated commit on 2026-09-26 and restored on
2026-11-16. The long narrative "current state" it used to carry is in git:
`git show a7e62733^:CLAUDE.md`.)

## Stack (decided, do not re-litigate)

- **Frontend:** React + TypeScript, Vite, pnpm workspaces
- **Desktop shell:** Electron (`apps/desktop`) — wraps the same frontend as the browser
  build
- **Backend:** Python 3.12 + FastAPI, managed with `uv`. The backend is the app's
  brain: agents, data, MCP integrations, websockets to the UI.
- **Extensibility:** built-in modules first. Every feature (chat, dashboard, notes,
  terminal, files) is an internal module registered through a central registry
  (commands, panels, keybindings). The public plugin API (`@horribledashboard/sdk` +
  marketplace) exposes the same contract to third-party frontend plugins, and
  `backend.sdk` exposes a server-side contract for backend plugins (routes, agent
  tools, `/ws` channels); both are trusted/unsandboxed in v1 — see
  docs/architecture/plugin-sdk.mdx and docs/architecture/backend-plugin-sdk.mdx.

## Layout

```
apps/
  web/            # Vite + React entry for the browser layout
  desktop/        # Electron shell reusing the same frontend
  mobile-android/ # Native Android companion (Kotlin + Compose); a peer on the
                  # fabric, not a second dashboard — built in Android Studio
  native-fps/     # native hassault client (Rust)
packages/
  core/           # TS core: module registry, layout engine, keymap, API client
  ui/             # shared React components and the workbench
  sdk/            # public plugin SDK (@horribledashboard/sdk): plugin contract + build preset
backend/          # FastAPI app: agents, modules' server side, websockets
website/          # Docusaurus site that publishes docs/
```

## Commands

- `uv run pytest` — backend tests
- `uv run uvicorn backend.app:app --reload --reload-dir backend --reload-exclude "logs/*" --port 8000` — backend dev server (the `--reload-dir`/`--reload-exclude` scoping keeps `logs/backend.log` writes from triggering a reload loop)
- `uv run ruff format <files>` and `uv run ruff check --fix <files>` — Python format/lint
- `uv add <pkg>` / `uv add --dev <pkg>` — Python dependencies (never pip)
- `uv sync --extra games-native` — install the native ViZDoom engine (the `vizdoom_toy` / `vizdoom_duel` games). It's an **optional extra** (lazy-imported), so the core app installs on every OS without it; add it only to run a Doom table locally. Prebuilt wheels: Windows amd64, Linux x86_64/aarch64, Apple-Silicon macOS 14+ (Intel/old macOS builds from sdist — needs `brew install cmake boost sdl2 openal-soft`). The Fly game-server image already pulls it.
- `uv sync --extra clip` — install **CLIP visual search** for the library (index media by appearance, not just alt text). Another **optional extra** (lazy-imported, gated by the `library.clipEnabled` setting): ONNX Runtime rather than torch, so it's ~60 MB and installs everywhere; the ViT-B/32 weights (~350 MB) download once on first use. Existing media needs `POST /api/library/reindex-clip` to backfill.
- `uv sync --extra llamacpp` — install **activation tracing** (`llama-cpp-python`, CPU-only). Another **optional extra**, lazy-imported and pinned with `==` because `backend/modules/llamacpp/ggml_abi.py` mirrors ggml's internal tensor struct. PyPI ships an sdist rather than wheels, so this builds llama.cpp and needs cmake plus a C++ compiler; without it the `traces` section says so and the chat provider is unaffected (it runs a downloaded `llama-server` binary and needs none of this).
- `uv sync --extra voice` — install the **Clubhouse voice agent**'s speech: local Whisper STT (`/api/agent/stt`) and Edge TTS (`/api/agent/tts`). An **optional extra** because Whisper needs torch (~1–2 GB); both services are lazy-imported and the two routes answer **503 with an install hint** rather than 500 without it, so the rest of the app is unaffected. Decoding the browser's WebM/Opus chunks also needs `ffmpeg` on PATH.
- `uv sync --extra geoip` — install **DB-IP GeoIP lookup** so the browser's Route view can plot traceroute hops on a map. Optional and lazy-imported; the DB-IP Lite City database (CC-BY, freely redistributable — unlike MaxMind GeoLite2, which needs an account and a license key) is downloaded once to `$HORRIBLE_DATA_DIR/geoip/dbip-city.mmdb`. Without it hops still list, they just have no coordinates.
- `uv sync --extra browser-engine && uv run playwright install chromium` — install the real headless-Chromium engine for the **browser** module's "full mode" (server-rendered, agentic). Also an **optional extra** (lazy-imported); the `playwright install` step fetches the ~150 MB per-OS Chromium once. Gated at runtime by `HORRIBLE_ENABLE_SERVER_BROWSER=1` — without the extra or the flag, the browser stays in the light iframe mode.
- `pnpm dev` — browser layout, full stack: starts the backend, the Vite UI (port 5173,
  proxies /api and /ws to 8000), **and** the central game server (:9090) together via
  `scripts/dev.mjs`. `--no-gameserver` (or `HORRIBLE_DEV_NO_GAMESERVER=1`) skips the
  game server. Sign-in points at the **hosted** server by default, same as
  `pnpm dev:desktop` and a packaged node — the bundled local server ships with no OAuth
  client id or secret, so forcing the node at it killed GitHub/Google sign-in in the
  browser layout while desktop kept working. `--local-gameserver` (or
  `HORRIBLE_DEV_LOCAL_GAMESERVER=1`) points the node at the bundled one when you want a
  self-contained stack. `pnpm dev:web` is UI-only; `pnpm dev:lan` exposes both on 0.0.0.0
  (peer fabric).
- `pnpm dev:desktop` — desktop layout (Electron; **spawns/supervises the backend
  itself** via `apps/desktop/src/backend.ts` — reuses one already running on :8000).
  Unlike `pnpm dev`, this **defaults to `0.0.0.0`** (the desktop node is the one you
  pair a phone with and run the peer fabric from); `--host 127.0.0.1` opts out. The
  knob is `HORRIBLE_DEV_HOST`, set by `scripts/dev-desktop.mjs` and read by both
  `vite.config.ts` and `bindHost()` in `backend.ts`, whose fallback stays loopback
  so a packaged build is never LAN-exposed.
- `node scripts/build-backend-runtime.mjs` — build the **bundled backend runtime** a
  packaged desktop app ships: a relocatable CPython (python-build-standalone, the
  distribution `uv` manages) with every locked dependency in its own `site-packages`
  and the `backend/` tree beside it, written to `apps/desktop/backend-runtime/`, which
  `electron-builder.json` ships. ~1 GB and a few minutes; git-ignored, and only needed
  to _package_ — `pnpm dev:desktop` runs the checkout. A real interpreter rather than
  PyInstaller because the import graph isn't knowable at build time (backend plugins
  come from pip entry points), and freezing it breaks that silently. See
  docs/architecture/releases.mdx.
- `pnpm typecheck`, `pnpm lint` — whole workspace, from the root
- `pnpm --filter @horrible/desktop test` — the desktop shell's own logic
- `pnpm build:hassault` / `pnpm build:hassault:debug` — build the **native
  hassault client** and hard-link the result into `apps/native-fps/bin/`, where
  the launcher looks. Use these rather than a bare `cargo build`: `pick_binary`
  starts the _newest_ of six candidate paths by mtime, so a sibling build (the
  other profile, or a stale `bin/` copy) can silently win and run a binary
  without the change you just made.
- **Android companion:** open `apps/mobile-android/` in Android Studio and run `app`.
  There's no committed Gradle wrapper yet, so there is no CLI build; `local.properties`
  is git-ignored (machine-local `sdk.dir`). The phone reaches the backend only when it's
  bound to the LAN (`pnpm dev:lan`) and the backend port is allowed through the firewall.

## Conventions

- TypeScript strict mode; no `any` without a comment explaining why.
- Pydantic models for every API boundary; the backend is the source of truth for
  shared types.
- **Never write `os.environ.get("HORRIBLE_DATA_DIR", ".data")`.** `backend/paths.py`
  is the one authority on where files live (`data_dir`, `config_dir`, `cache_dir`,
  `log_dir`). That inline default was relative to the **cwd**, which is set by
  whichever launcher started the backend, so the same code resolved to different
  directories under `pnpm dev`, the desktop supervisor and a packaged build — a user's
  llama.cpp install simply vanishing. `paths` resolves from the source tree instead:
  env override → `<repo>/.data` in a checkout → the per-OS location. See
  docs/architecture/data-directories.mdx.
- New features go through the module registry — a module declares its commands,
  panels, and keybindings in one place; nothing reaches into another module's
  internals. See `.claude/skills/new-module/SKILL.md` before adding a feature.
- **A pane never assumes where it is.** Open and reveal panes through
  `registry.openPanel`, `revealSection` and friends, never by poking layout geometry.
  A pane that opts into pop-out (`popout: true`) must use `usePaneWindow()` instead of
  the global `window`/`document`; see docs/architecture/windowing.mdx.
- **Never install a `keydown` listener in a component.** `packages/core/src/keymap/`
  is the one keyboard authority: declare a `KeybindingDecl` (with a `when` clause
  over the closed context-key vocabulary if it should be pane-scoped). A pane that
  genuinely needs the keyboard — a game, a terminal, the server-rendered browser —
  takes **capture** (`useCapture`, or `capture` in its manifest) so the shell knows
  to suppress its own bindings, and Escape unwinds through one ordered ladder
  instead of every component racing for it. Bindings the host steals
  (`mod+1..9` is browser tab switching and never arrives) are declared in
  `keymap/reserved.ts` and split by `hosts`/`platforms`. See
  docs/architecture/keybindings.mdx.
- Browser and desktop layouts share one codebase; never fork a component per
  platform — branch on a platform capability check instead.
- Formatting/linting is automatic: a PostToolUse hook runs ruff (Python), prettier +
  eslint (TS/JS), and rustfmt (Rust) on every file you edit. Don't hand-format.
- **Clubhouse App Versioning:** Clubhouse enforces the client's version headers (since 2026-09-10 a stale one gets "Please upgrade your app", and SMS codes come back `is_verified: false`). There are **two** values: `CH-AppVersion` (`26.11.12`) and `CH-AppBuild` — the **numeric build** (`1039311`), which also goes in the `User-Agent`. Read them with `aapt dump badging <apk>` — `versionName` and `versionCode` are the pair (verified: the 26.08.30 APK returns exactly the `1038152`/`26.08.30` that had been read out of `defpackage/cb1.java` by hand), then cross-check that the build literal occurs as often as the superseded one did (three times, one in the dex holding `CH-AppBuild` — R8 moves them between `classesN.dex` files across releases) and that the old build is gone. Update both in `backend/modules/clubhouse/auth_helper/Program.cs` and `routes._headers`, then delete `backend/modules/clubhouse/auth_helper/bin/ch-auth-helper.exe` so the backend recompiles it. `login did not pass token validation` on `start` is the separate anti-bot gate, not the version. **`check_for_update` cannot confirm a bump** — it answers `has_update: false` for a stale pair too — and a gated route probed from outside a room fails the channel check first, so the gate is only observable from inside a room.

## Documentation (docs/)

`docs/` documents the layout shell and every module — see
[docs/README.mdx](docs/README.mdx) for the index and the full sync policy. The short
version: adding or changing a module, panel, command, capability, backend route, or
layout-shell behavior must update the matching `docs/` page **in the same change**
(new module → new `docs/modules/<name>.mdx`). A Stop hook flags code changes under
`apps/`, `packages/`, or `backend/` that don't touch `docs/`; pure refactors are
exempt — just say so.

The docs are authored as **MDX with Mermaid diagrams** and published as a
[Docusaurus](https://docusaurus.io) site (`website/`, which reads this `docs/`
tree directly). `pnpm --filter @horrible/docs build` builds it locally; pushing to
`main` deploys to GitHub Pages via `.github/workflows/docs.yml`.

## UI and Design Standards

Apply a modern, developer-first Kombai aesthetic across the UI components:

- Elevation & Color:
  - Surface: Dark slate background (#16181D) with subtle 1px border (#262934) and inset top shadow.
  - Accentuate cards on hover with a crisp border highlight (#3B82F6 or #6366F1 at 40% opacity).
- Typography & Badging:
  - Use Geist/Inter for UI text and Geist Mono/JetBrains Mono for IDs, paths, and registry keys.
  - Replace raw badge borders with soft pill chips (e.g. green status dot + text on translucent green fill).
- Command Snippets:
  - Display commands (uvx, npx, http endpoints) inside recessed, rounded code containers with syntax-colored protocol prefixes.
- Compact Dev-Tool Form Controls:
  - The 30px height is **already applied**, by `packages/ui/src/controls.css`, to every
    `input`/`select`/`textarea` in the app — you do not implement it per form, and
    `--control-h` is the one place it changes.
  - **Never set vertical padding on an `input` or `select`.** Their height is _fixed_,
    so padding does not make the control roomier, it shrinks the content box inside it:
    `padding: 0.5rem` leaves `30 − 16 − 2 = 12px` for 12.8px text, and the label draws
    low and clips along the bottom. Style horizontal padding only (`padding: '0 0.6rem'`).
    A text input under that padding merely looks tight while a `<select>` visibly clips
    (it also reserves room for its arrow), so this reads as "the dropdowns are broken"
    and the inputs beside them are wrong too. `packages/core/src/__tests__/control-padding.test.ts`
    fails the build on a new one; `textarea` is exempt (it is `height: auto` and must grow).
  - Give every text `<input>` an explicit `type`. `controls.css` matches
    `input[type='text']` and friends, and an attribute selector does not match a default
    — a bare `<input>` used to opt itself out of the height rule silently.
  - **A classless `<button>` is a standard 30px control; give it a class if it is
    something else.** `<button>` is this codebase's generic interactive element — rows,
    chips, tabs, menu items, cards — so the height rule is scoped to
    `button:not([class])` plus a short list of named action-button classes rather than
    to every button. Consequences when writing one: a plain action button needs no
    styling to be the right height; a button that is really a row or a chip must carry a
    class (it would be stretched otherwise); and `.btn-mini` is the opt-in escape hatch
    for a classless button that is genuinely tiny. Vertical padding is wrong here for the
    same reason as on an input — it composes with the font size, which is exactly how the
    same declaration produced 21px and 34px buttons.
  - Group related inputs into inline segmented blocks.
  - Use high-contrast primary buttons paired with subtle ghost secondary buttons and clean micro-icons.
