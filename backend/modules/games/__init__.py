"""Node games module: bridges the node's agent to the central game server.

- `client.games_client` — the process-global connection + auto-play loop.
- `channel.handle_games_message` — the browser-facing `/ws` `games` channel.
- `agent_tools.register_agent_tools` — the manual-play agent tools.
- `routes.router` — `/api/games` status + catalog.

See docs/modules/games.mdx.
"""

from __future__ import annotations

from backend.modules.games.agent_tools import register_agent_tools
from backend.modules.games.channel import drop_games_conn, handle_games_message
from backend.modules.games.client import games_client
from backend.modules.games.routes import router

__all__ = [
    "drop_games_conn",
    "games_client",
    "handle_games_message",
    "register_agent_tools",
    "router",
]
