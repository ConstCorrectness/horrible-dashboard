"""The **central game server**: an authoritative referee for agent-vs-agent games.

Unlike the peer fabric (self-certifying Ed25519 nodes, no accounts), this is a
deliberately centralized service — the referee owns game state, resolves chance
(shuffles) with its own RNG, and validates every move, which is the only way to
run hidden-information games (poker) without trusting the players. A node connects
its own agent over `/game-ws`; the server never runs anyone's model.

Run separately from a node's own backend:

    uv run uvicorn backend.games_server.app:app --port 9200

Phase 1 uses a dev token (the token *is* the account id). OAuth + JWT land in a
later phase. See docs/architecture/game-server.mdx.
"""

from __future__ import annotations

from backend.games_server.hub import GameHub
from backend.games_server.referee import Referee

__all__ = ["GameHub", "Referee"]
