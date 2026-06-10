![horrible-dashboard](./banner.svg)

# horrible-dashboard

A unified one-stop app for everything — emacs for the agentic era. One React
frontend, two layouts: the browser (`apps/web`) and a cross-platform desktop app
(`apps/desktop`, Tauri). A Python FastAPI backend (`backend/`) is the brain.

## Quick start

```sh
uv sync && pnpm install
uv run uvicorn backend.app:app --reload --port 8000   # backend
pnpm dev                                              # browser layout → http://localhost:5173
# desktop layout: cd apps/desktop && pnpm dev
```

Architecture and module docs live in [docs/](docs/README.md). Conventions for
contributors (human or agent) are in [CLAUDE.md](CLAUDE.md).
