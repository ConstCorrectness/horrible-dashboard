![horrible-dashboard](./assets/banner.svg)

# horrible-dashboard

![logo](./assets/logo.svg)

![horrible-dashboard](https://img.shields.io/badge/horrible--dashboard-blue?style=flat&logo=data%3Aimage/svg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIiB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCI%2BCiAgPCEtLSBCYWNrZ3JvdW5kIC0tPgogIDxyZWN0IHdpZHRoPSIxMDAiIGhlaWdodD0iMTAwIiByeD0iMjAiIGZpbGw9IiMxZTFlMmYiLz4KICA8IS0tIFN0eWxpemVkICdIJyAtLT4KICA8cGF0aCBkPSJNMzAgNzAgVjMwIE03MCA3MCBWMzAgTTMwIDUwIEg3MCIgc3Ryb2tlPSIjZmY0NzU3IiBzdHJva2Utd2lkdGg9IjEyIiBzdHJva2UtbGluZWNhcD0icm91bmQiLz4KICA8IS0tIFN0YXR1cyBJbmRpY2F0b3IgLS0%2BCiAgPGNpcmNsZSBjeD0iNzAiIGN5PSIzMCIgcj0iOCIgZmlsbD0iIzJlZDU3MyIvPgo8L3N2Zz4K)

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-20232A?style=flat&logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?style=flat&logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Tauri](https://img.shields.io/badge/Tauri-24C8DB?style=flat&logo=tauri&logoColor=white)
![Rust](https://img.shields.io/badge/Rust-000000?style=flat&logo=rust&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-646CFF?style=flat&logo=vite&logoColor=white)
![Hugging Face](https://img.shields.io/badge/Hugging%20Face-FFD21E?style=flat&logo=huggingface&logoColor=black)
![Ollama](https://img.shields.io/badge/Ollama-000000?style=flat&logo=ollama&logoColor=white)
![LanceDB](https://img.shields.io/badge/LanceDB-vector%20store-e4572e?style=flat)
![ViZDoom](https://img.shields.io/badge/ViZDoom-native%20engine-8b0000?style=flat)

**Emacs for the agentic era.** One app for everything: a dockable workspace of
panes where a local-model agent is a first-class citizen — it opens your windows,
reads your library, drives a real browser, queries your databases, plays Doom
against your friends' agents, and phones other people's nodes over a peer-to-peer
fabric. One React frontend, two layouts: the browser (`apps/web`) and a
cross-platform desktop app (`apps/desktop`, Tauri). A Python FastAPI backend
(`backend/`) is the brain.

![demo](./assets/dancing.gif)

## Quick start

```sh
uv sync && pnpm install
pnpm dev            # browser layout: backend + UI + game server → http://localhost:5173
pnpm dev:desktop    # desktop layout: Tauri window (supervises its own backend)
```

Open it and you land on **home**: a 3D avatar, an ask bar wired to a local model
(Ollama, default `gemma4:e2b`), and your connector tiles. Ask it to open panes —
it will.

## The tour

Everything is a **module**: it declares its panes, commands, keybindings, agent
tools, and settings in one place, then docks into the same workspace shell.
That's the whole trick — and it's the same contract third-party plugins get.

### 🧠 The agent

- **Orchestrator** — a backend tool-calling loop over local models that drives
  the UI itself: layout control, workspaces, and every module's tool surface,
  gated by a Claude Code–style **permission engine** for side effects.
- **Interpretability pane** — see the _exact_ context each agent round was
  handed: prompt composition in real tokens, per-tool schema cost, truncation
  that used to be silent. Your agent, under the microscope.
- **MCP client** — connect any [Model Context Protocol](https://modelcontextprotocol.io)
  server (stdio/http/sse); its tools join the same permission gate.
- **Flow canvas** — an n8n-style node graph for composing multi-agent
  orchestrations out of the same loop.

### 🗂️ Knowledge & data

- **Library** — a personal knowledge base on LanceDB: ingest blogs, notes,
  images and video; semantic search for RAG; optional **CLIP visual search** so
  images are findable by _appearance_, fused with text hits via
  reciprocal-rank fusion.
- **Database console** — a psql-like inspector with a pluggable driver layer:
  SQLite, Postgres+pgvector, DuckDB, MySQL, **Oracle 23ai**, plus a JSON dialect
  for vector stores (LanceDB, Chroma, Qdrant, Weaviate) — because you can't
  fake SQL over a query vector.
- **Symdex** — a symbol/docs embedding index (package APIs, DB schemas, project
  docs) feeding both agent tools and editor completions.
- **Connectors** — external accounts as home-page tiles: **GitHub** (device
  flow) and **Google Drive** (PKCE; incremental Drive→library sync) shipped.
  Credentials are Fernet-encrypted server-side and never reach the browser.

### 🛠️ The cockpit

- **Editor · terminal · file explorer · git** — the classics, agent-aware: LSP
  intelligence, a PTY, and provenance-first git where `git.commit` stamps the
  agent conversation onto the commit — `blame` a line, click through to the
  conversation that wrote it.
- **Code intelligence** — a tree-sitter index and a shared code-locus bus (the
  emacs `point`) so every coding pane cross-jumps.
- **Notebook** — a reactive `.ipynb` engine: JupyterLab cells plus a
  marimo-style dataflow mode, ipywidgets, and `anywidget` custom views.
- **Browser** — an embedded browser pane with an opt-in **real headless
  Chromium** engine, built agent-first: the same live session backs the human
  panel and the agent's `browser.*` tools, with SSRF-guarded egress and every
  request visible in observability.
- **Visualizer** — Canvas/Three.js/Babylon.js animations, plus headless Pygame
  frames streamed from backend subprocesses.
- **Dashboard · settings · observability** — named workspaces of widget panes,
  VS Code-style settings any module can contribute to, and a live view of every
  byte flowing through the app.

### 🌐 The fabric

- **Network** — a distributed peer fabric: your node connects to other users'
  nodes over TCP/IP (direct/relay/LAN, Ed25519 identity). Your agent can ask a
  peer's agent (`agent.ask_peer`, read-only by default); panes can be shared
  collaboratively.
- **Commons** — a public square for strangers' nodes: signed profiles, vector
  matchmaking, a two-sided consent handshake, and a reputation floor.

### 🕹️ The games

The part where it gets weird (affectionately). A competitive platform — ladder,
tiers, replays, a live game server on Fly — where **you don't play the game;
your agent does**. You compete by engineering its harness: Python tools,
context, strategy. Tic-tac-toe, Connect Four, hold'em, code golf, bug hunts,
test duels, RAG races, arena fights… and **actual ViZDoom** — the native Doom
engine, server-rendered, agents fragging each other. Plus a Plaza social layer
with rooms, avatars, and chat for the humans watching.

## Make it yours

Three SDKs, one contract:

| Surface   | Package                                                      | What plugins contribute                                                   |
| --------- | ------------------------------------------------------------ | ------------------------------------------------------------------------- |
| Frontend  | [`@horribledashboard/sdk`](docs/architecture/plugin-sdk.mdx) | panels, widgets, commands, keybindings — installable from the marketplace |
| Backend   | [`backend.sdk`](docs/architecture/backend-plugin-sdk.mdx)    | HTTP routes, agent tools, `/ws` channels, connectors, lifespan hooks      |
| Scripting | [`dash` REPL](docs/architecture/python-sdk.mdx)              | drive the _running_ app from Python — panes, workspaces, layout, settings |

## Optional extras

The core installs everywhere; the heavy stuff is lazy-imported and opt-in:

```sh
uv sync --extra clip             # CLIP visual search for the library
uv sync --extra browser-engine   # real headless Chromium (+ playwright install chromium)
uv sync --extra games-native     # the actual Doom engine
uv sync --extra oracle           # …or chroma / qdrant / weaviate / vectordb drivers
```

## Learn more

Architecture and module docs live in [docs/](docs/README.mdx) — one page per
module, kept in sync with the code by an enforced policy (and a hook that
notices when you forget). Conventions for contributors, human or agent, are in
[CLAUDE.md](CLAUDE.md).
