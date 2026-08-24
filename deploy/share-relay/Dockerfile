# Container image for the public share relay (backend/share_relay).
#
# Build context is the repo ROOT (it needs pyproject.toml, uv.lock, and backend/).
# `fly.share.toml` at the repo root points here, so deploy from the root:
#   fly deploy -c fly.share.toml
#   docker build -f deploy/share-relay/Dockerfile -t horrible-share-relay .
#
# Deliberately a *separate* image and app from the game server. The relay is the one
# component strangers talk to, it scales on a different axis from everything else, and
# a relay falling over must never be able to take the game server down with it.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# aiortc's media stack. `av` ships manylinux wheels (no compiler needed), but it
# dlopens the FFmpeg shared libraries at import, and `pylibsrtp`/`cryptography` need
# libsrtp and libssl present. Without these the image builds cleanly and then fails
# at the first WHIP offer -- which is the worst possible place to discover it.
#
# `ffmpeg` is the RTMP restream path (Phase 6). Optional at runtime -- the relay
# reports `can_restream: false` and answers 503 with a reason without it, the same
# way karaoke treats transposition -- but this image is where restreaming is meant
# to happen, so it is present here rather than left to chance.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsrtp2-1 libssl3 libopus0 libvpx7 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Layer 1 -- dependencies only, from the lockfile. Cached until pyproject/uv.lock change.
# `--extra webrtc` pulls aiortc: this image *is* the WebRTC endpoint, so unlike a node
# (where the extra is optional and lazy-imported) it is required here.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra webrtc

# Layer 2 -- the source. The relay imports only backend.share_relay plus the light
# backend/__init__; it deliberately does not import backend.app or any node module.
COPY backend ./backend

EXPOSE 9100

# --no-sync: the venv is already built above; don't re-resolve at container start.
# One worker: the registry and the live rooms are process memory, and a second worker
# would answer WHEP for streams it has never seen. Scale by adding machines (with
# fly-replay pinning a token to one) rather than workers inside one machine.
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.share_relay.app:app", \
     "--host", "0.0.0.0", "--port", "9100", "--workers", "1"]
