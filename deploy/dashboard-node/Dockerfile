# One user's dashboard backend, as the hosted hub spawns it (backend/hub/spawner).
#
# Build context is the repo ROOT:
#   docker build -f deploy/dashboard-node/Dockerfile -t horrible-dashboard-node .
#
# The hub runs one container of this image per signed-in user, with that user's own
# volume at /data, on an internal network with no published port. The container is
# the user's sandbox — the terminal, notebooks and the agent's shell tool all run
# here — so it runs as an unprivileged user, and the hub sets cpu/memory/pid limits.
#
# It does not serve the frontend: the hub does (deploy/hub/Dockerfile). Every request
# must carry the hub's per-container token (HORRIBLE_INSTANCE_TOKEN, injected by the
# spawner at create time, never baked in) — see backend/hosted.py.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# What the user's terminal expects to find: a real shell, git, and ps/kill.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Layer 1 — dependencies only, from the lockfile. No extras: voice, CLIP, the
# server-side browser and the GPU stacks are opt-in per deployment, not per user.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Layer 2 — the backend source.
COPY backend ./backend

RUN useradd --create-home --uid 10001 --shell /bin/bash horrible \
    && mkdir -p /data \
    && chown horrible:horrible /data

# Everything the user owns lives on the per-user volume: data, config (settings,
# keymap), cache, logs — and HOME, so `~/Projects`, the file explorer's default
# root, and the shell's dotfiles survive a container being recreated.
ENV HORRIBLE_PROFILE=hosted \
    HORRIBLE_DATA_DIR=/data/data \
    HORRIBLE_CONFIG_DIR=/data/config \
    HORRIBLE_CACHE_DIR=/data/cache \
    HORRIBLE_LOG_DIR=/data/logs \
    SECRETS_KEY_PATH=/data/config/secrets.key \
    HORRIBLE_ENABLE_SERVER_BROWSER=0 \
    HORRIBLE_RELAY_URL=off \
    HOME=/data/home \
    SHELL=/bin/bash

USER horrible
EXPOSE 8000

# --no-sync: the venv is already built above; don't re-resolve at container start.
# No --reload: there is no checkout to watch.
#
# The bind address comes from the spawner, because the two hosts need opposite
# families and one socket cannot serve both: asyncio makes a `::` socket IPv6-only.
# A Docker bridge reaches the instance over IPv4 (the default); Fly's private
# network is IPv6-only, so the Fly spawner sets HORRIBLE_BIND_HOST=::.
CMD ["sh", "-c", "exec uv run --no-sync uvicorn backend.app:app --host \"${HORRIBLE_BIND_HOST:-0.0.0.0}\" --port 8000"]
