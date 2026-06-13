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

A unified one-stop app for everything — emacs for the agentic era. One React
frontend, two layouts: the browser (`apps/web`) and a cross-platform desktop app
(`apps/desktop`, Tauri). A Python FastAPI backend (`backend/`) is the brain.

![demo](./assets/dancing.gif)

## Quick start

```sh
uv sync && pnpm install
uv run uvicorn backend.app:app --reload --port 8000   # backend
pnpm dev                                              # browser layout → http://localhost:5173
# desktop layout: cd apps/desktop && pnpm dev
```

Architecture and module docs live in [docs/](docs/README.md). Conventions for
contributors (human or agent) are in [CLAUDE.md](CLAUDE.md).
