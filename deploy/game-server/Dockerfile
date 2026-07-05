# Container image for the central game server (backend/games_server).
#
# Build context is the repo ROOT (it needs pyproject.toml, uv.lock, and backend/).
# The repo-root fly.toml points here via [build].dockerfile, so deploy from the root:
#   fly deploy
#   docker build -f deploy/game-server/Dockerfile -t horrible-game-server .
#
# Only backend/ is copied in; the frontend and local .data/logs are excluded by the
# repo-root .dockerignore, so the image stays small (no torch/ML deps in this project).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Layer 1 — dependencies only, from the lockfile. Cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Layer 2 — the server source. The game server imports only backend.games_server and
# backend.games_engine (plus the light backend/__init__).
COPY backend ./backend

# Production defaults. Real secrets (GAMES_JWT_SECRET, GAMES_GITHUB_CLIENT_ID) are
# injected by the host (`fly secrets set ...`), never baked into the image.
ENV HORRIBLE_DATA_DIR=/data \
    GAMES_ALLOW_DEV_AUTH=0

EXPOSE 9200

# --no-sync: the venv is already built above; don't re-resolve at container start.
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.games_server.app:app", \
     "--host", "0.0.0.0", "--port", "9200"]
