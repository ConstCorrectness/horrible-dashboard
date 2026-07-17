# horrible-dashboard

A unified one-stop app for everything — "emacs for the agentic era". One web UI serves
two layouts: a browser app and a cross-platform desktop app (Tauri shell wrapping the
same frontend).

## Current state

Scaffolded and verified: pnpm monorepo (apps/web, apps/desktop, packages/core,
packages/ui), FastAPI backend, the **dashboard module** end to end, and the
**agent module** — a Gemini-style `home` view (default on open) with
3D avatar, local-model onboarding via Ollama (default `gemma4:e2b`), and an ask
bar wired to the **agent orchestrator**: a backend tool-calling loop (Ollama
`/api/chat` with tools) that drives the UI — first slice is **layout control**
(open/close panes, manage workspaces) with tool calls relayed to the frontend
over the shared `/ws` `agent` channel. See docs/modules/agent-chat.mdx. The `workspace` view is a **dockable window manager** (dockview,
wrapped): module **panes — panels and widgets alike** — open as tabbed/split/
floating windows. **Widgets are first-class panes** (no separate grid); the
**dashboard is the default seeded one of several named workspaces** you switch
between via a tab strip, each layout persisted server-side. A minimal `scratch`
panel is the reference non-singleton panel. A `clubhouse` module onboards a
Clubhouse account via a widget (unofficial API, token held server-side). An `observability` module shows live data flow
(client/inbound/outbound I/O) — instrumented at the chokepoints, streamed over
the shared `/ws` socket. A **public plugin SDK** (`packages/sdk`,
`@horribledashboard/sdk`) plus a `marketplace` module let third-party frontend plugins
(panels/widgets/commands/keybindings) be installed from a catalog and loaded at
boot — see docs/architecture/plugin-sdk.mdx. A **backend plugin SDK** (`backend/sdk`,
`backend.sdk`) is the server-side counterpart: plugins contribute HTTP routes,
agent tools, `/ws` channels, lifespan hooks, and `dash` REPL facades, discovered
from bundled dirs, `HORRIBLE_PLUGINS_DIR`, and pip entry points — see
docs/architecture/backend-plugin-sdk.mdx. The **`dash`** Python REPL handle scripts
the running app (panes/workspaces/layout/I-O/settings; `dash.help()`) — see
docs/architecture/python-sdk.mdx. A `settings` module gives a
VS Code–style settings page where any module or plugin contributes its own
settings (declared in its manifest, read live via `useSetting`/`host.settings`,
overrides persisted server-side) — see docs/modules/settings.mdx. Note `GET /api/settings` returns the whole bag to the browser, so a **client secret / API key must never be a setting** (a client id is fine — it's public). A **connectors** module is the integration surface: external accounts the node holds credentials for, rendered as a **row of tiles above the ask bar on the home page**. A connector owns its connect flow (`oauth` device/redirect, `api-key`, or a `custom` multi-step form), its credential (Fernet-encrypted in `secrets.db` under `connector:<id>`, **never handed to the browser**; the master key lives outside `$HORRIBLE_DATA_DIR` at `~/.horrible/secrets.key` so copying the data dir leaks nothing), and the agent tools it unlocks. Contributed via `host.add_connector` (`backend/sdk`), so backend plugins register like built-ins. **GitHub is implemented** (device flow — OAuth Apps have no PKCE, so an authorization-code flow would mean shipping a secret; `read:user`+`repo`; `github.searchCode/searchRepos/listRepos/readFile/listIssues`). **Google is implemented** (loopback authorization-code + **PKCE**, because Google's device flow can't carry `drive.readonly`; scope is Drive read-only only; `google.driveSearch/driveRead/syncDrive`) and is deliberately **bring-your-own-client** — `drive.readonly` is a Google _restricted_ scope, so a published app would need verification + an annual CASA assessment, and an app in Testing expires refresh tokens after **7 days**; with your own Cloud project you're your own sole test user. `google.syncDrive` / `POST /api/connectors/google/sync` walks Drive into a **library** as `note` sources (Google Docs exported, PDFs parsed with **pypdf** — _not_ PyMuPDF, which is AGPL), paginated and **incremental** via Drive's `changes` feed, with a `google_drive_files` map in `app.db` so a re-sync replaces rather than duplicates. A connector's `id` **must** equal its agent-tool prefix — the orchestrator groups tools by name prefix, and `AgentTool.group` does _not_ name the group. Distinct from the games sign-in, which is identity-only: it runs on the separate game server and discards the provider token — see docs/modules/connectors.mdx. A **database** module is a plug-and-play, psql-like database inspector: a SQL console pane that queries any connected database through a pluggable driver layer (sqlite / postgres+pgvector / duckdb / mysql), with the node's own SQLite database (`$HORRIBLE_DATA_DIR/app.db` — library sources, browser history, code symbols, tasks) as the built-in `app` connection. Distinct from the app's local **vector store** (**LanceDB** under `$HORRIBLE_DATA_DIR/lancedb`, one table per collection, also used by agent-commons matchmaking), which is a directory, not a SQL-console-attachable file — see docs/modules/database.mdx. A **library** module is a personal knowledge base built on that vector store: ingest website blogs (by URL, extracted with trafilatura), notes, and **images/videos** (PDF/EPUB deferred) as **sources + chunks** — a `library_sources` catalog in `app.db` plus per-chunk rows in the library's LanceDB table — then semantic-search a library for RAG, with live ingestion status on the `library` `/ws` channel. The default embedder is **text-only**, so media is embedded by _proxy_ (alt text, caption, nearby heading harvested from the live DOM) and referenced by URL rather than copied. Optional **CLIP visual search** (`uv sync --extra clip` + the `library.clipEnabled` setting) adds a second space so media is findable by _appearance_ — undescribed images included: a 512-dim ViT-B/32 ONNX vector in an additive `<library>__clip` sibling table (LanceDB fixes vector width per table), fused with the text hits by Reciprocal Rank Fusion. The agent tools (`library.search/listSources/addSource`) are the retrieval half of RAG; `browser.save` is the write half — see docs/modules/library.mdx. A **visualizer** module renders HTML5 Canvas, Three.js, and Babylon.js dynamic client-side animations, and streams headless Pygame frames from backend subprocesses — see docs/modules/visualizer.mdx. A **browser** module is an **embedded web browser** pane with two engines: a light cross-origin `<iframe>` (default), and — opt-in via `HORRIBLE_ENABLE_SERVER_BROWSER=1` + the `browser-engine` extra — a **real headless Chromium** driven on the local backend and server-rendered to the pane (JPEG frames over `/ws`, interactions relayed back, persistent cookies/cache), built **agent-first**: the same live session backs the human panel and the agent tools (`browser.read`/`snapshot`/`scrape`/`click`/`type`/`media`/`save`), so the agent is handed the DOM content + an accessibility snapshot and can scrape, act, and **remember** — `browser.save` files a page or its images/videos into a library (the write half of RAG). Every request the Chromium makes is recorded as a `browser` I/O event (resource type + allowed/blocked egress verdict) and shown live in the pane's 📡 Network view and the observability panel. Egress is SSRF-guarded; reader mode + bookmarks/history persist server-side — see docs/modules/browser.mdx. A **network** module is the **distributed peer fabric**: a process-global `PeerHub` lets this backend node connect to other users' nodes over TCP/IP (hybrid direct/relay/LAN transports, Ed25519 node identity, settings-driven trust), so users collaborate via **agent-to-agent** (`agent.ask_peer` — your agent asks a peer's agent, gated read-only by default) and **collaborative shared panes** (a `collab` channel; scratch is the reference) — see docs/modules/network.mdx and docs/architecture/distributed.mdx. An experimental Electron shell lives on the `electron-shell` branch. Remaining modules (editor, terminal, files, full chat cockpit) are unimplemented — see docs/ for their designs.

## Stack (decided, do not re-litigate)

- **Frontend:** React + TypeScript, Vite, pnpm workspaces
- **Desktop shell:** Tauri (Rust) — wraps the same frontend as the browser build
- **Backend:** Python 3.12 + FastAPI, managed with `uv`. The backend is the app's
  brain: agents, data, MCP integrations, websockets to the UI.
- **Extensibility:** built-in modules first. Every feature (chat, dashboard, notes,
  terminal, files) is an internal module registered through a central registry
  (commands, panels, keybindings). The public plugin API (`@horribledashboard/sdk` +
  marketplace) exposes the same contract to third-party frontend plugins, and
  `backend.sdk` exposes a server-side contract for backend plugins (routes, agent
  tools, `/ws` channels); both are trusted/unsandboxed in v1 — see
  docs/architecture/plugin-sdk.mdx and docs/architecture/backend-plugin-sdk.mdx.

## Target layout

```
apps/
  web/            # Vite + React entry for the browser layout
  desktop/        # Tauri app (src-tauri/) reusing the same frontend
packages/
  core/           # TS core: module registry, command palette, keybindings, API client
  ui/             # shared React components and panel/docking system
  sdk/            # public plugin SDK (@horribledashboard/sdk): plugin contract + build preset
backend/          # FastAPI app: agents, modules' server side, websockets
```

First modules, in priority order: agent chat cockpit, dashboard/widgets, notes/editor
buffers, terminal + file explorer.

## Commands

- `uv run pytest` — backend tests
- `uv run uvicorn backend.app:app --reload --reload-dir backend --reload-exclude "logs/*" --port 8000` — backend dev server (the `--reload-dir`/`--reload-exclude` scoping keeps `logs/backend.log` writes from triggering a reload loop)
- `uv run ruff format .` and `uv run ruff check --fix .` — Python format/lint
- `uv add <pkg>` / `uv add --dev <pkg>` — Python dependencies (never pip)
- `uv sync --extra games-native` — install the native ViZDoom engine (the `vizdoom_toy` / `vizdoom_duel` games). It's an **optional extra** (lazy-imported), so the core app installs on every OS without it; add it only to run a Doom table locally. Prebuilt wheels: Windows amd64, Linux x86_64/aarch64, Apple-Silicon macOS 14+ (Intel/old macOS builds from sdist — needs `brew install cmake boost sdl2 openal-soft`). The Fly game-server image already pulls it.
- `uv sync --extra clip` — install **CLIP visual search** for the library (index media by appearance, not just alt text). Another **optional extra** (lazy-imported, gated by the `library.clipEnabled` setting): ONNX Runtime rather than torch, so it's ~60 MB and installs everywhere; the ViT-B/32 weights (~350 MB) download once on first use. Existing media needs `POST /api/library/reindex-clip` to backfill.
- `uv sync --extra browser-engine && uv run playwright install chromium` — install the real headless-Chromium engine for the **browser** module's "full mode" (server-rendered, agentic). Also an **optional extra** (lazy-imported); the `playwright install` step fetches the ~150 MB per-OS Chromium once. Gated at runtime by `HORRIBLE_ENABLE_SERVER_BROWSER=1` — without the extra or the flag, the browser stays in the light iframe mode.
- `pnpm dev` — browser layout, full stack: starts the backend, the Vite UI (port 5173,
  proxies /api and /ws to 8000), **and** the central game server (:9200) together via
  `scripts/dev.mjs`. `--no-gameserver` (or `HORRIBLE_DEV_NO_GAMESERVER=1`) skips the
  game server. `pnpm dev:web` is UI-only; `pnpm dev:lan` exposes both on 0.0.0.0 (peer fabric).
- `pnpm dev:desktop` — desktop layout (Tauri; **spawns/supervises the backend
  itself** via `src-tauri/src/backend.rs` — reuses one already running on :8000)
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
- **Clubhouse App Versioning:** The Clubhouse API requires a current Android app version header (e.g. `26.07.12`). If auth fails with `login did not pass token validation`, update `AppVersion` in `backend/modules/clubhouse/auth_helper/Program.cs` and `routes.py`, then delete `backend/modules/clubhouse/auth_helper/bin/ch-auth-helper.exe` so the backend forces a recompile.

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
