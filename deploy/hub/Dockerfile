# The hosted hub (backend/hub): sign-in, one instance per user, reverse proxy.
#
# Build context is the repo ROOT, after building the frontend on the host (building
# it here would need the whole pnpm workspace):
#   pnpm --filter @horrible/web build
#   docker build -f deploy/hub/Dockerfile -t horrible-dashboard-hub .
#
# The hub drives the Docker daemon to create users' containers, so it is normally
# run with the daemon's socket mounted — see deploy/hub/docker-compose.yml, and
# docs/architecture/hosted-hub.mdx on what that grants.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# `--extra hub`: the Docker SDK the spawner uses.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra hub

COPY backend ./backend
COPY apps/web/dist ./apps/web/dist

ENV HUB_DB_PATH=/hub-data/hub.db \
    HUB_WEB_DIST=/app/apps/web/dist

EXPOSE 8080

CMD ["uv", "run", "--no-sync", "uvicorn", "backend.hub.app:app", \
     "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
