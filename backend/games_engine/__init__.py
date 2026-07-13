"""OpenSpiel-shaped turn-based game engines, shared by the authoritative game
server and (optionally) a node.

One `GameState` interface spans perfect-information board games (tic-tac-toe,
connect four, chess) and imperfect-information games with chance and betting
(No-Limit Hold'em). The key ideas that make that possible:

- **`current_player()`** returns a seat index, or the sentinel `CHANCE` (a random
  event the *server* must resolve — e.g. shuffling a deck) or `TERMINAL`.
- **`observation(player)`** is what *that* player is allowed to see; it hides every
  opponent's private state (hole cards). **`public_state()`** is the spectator view
  and hides *all* hidden information. The server never sends a player anything but
  their own observation, so a malicious node can't learn an opponent's cards.
- **`legal_actions(player)`** enumerates the moves the rules permit, so the agent
  only ever *chooses among legal moves* — it never computes legality itself.

See docs/architecture/game-server.mdx.
"""

from __future__ import annotations

from backend.games_engine.base import (
    CHANCE,
    TERMINAL,
    WORK,
    Action,
    GameSpec,
    GameState,
    get_game,
    list_games,
    register_game,
)

# Importing a game module registers it via `register_game` as a side effect.
from backend.games_engine import arena  # noqa: F401  (registration side effect)
from backend.games_engine import bug_hunt  # noqa: F401  (registration side effect)
from backend.games_engine import code_golf  # noqa: F401  (registration side effect)
from backend.games_engine import connect_four  # noqa: F401  (registration side effect)
from backend.games_engine import fighter  # noqa: F401  (registration side effect)
from backend.games_engine import holdem  # noqa: F401  (registration side effect)
from backend.games_engine import rag_race  # noqa: F401  (registration side effect)
from backend.games_engine import tabular_fe  # noqa: F401  (registration side effect)
from backend.games_engine import test_duel  # noqa: F401  (registration side effect)
from backend.games_engine import tictactoe  # noqa: F401  (registration side effect)
from backend.games_engine import vizdoom_toy  # noqa: F401  (registration side effect)

__all__ = [
    "CHANCE",
    "TERMINAL",
    "WORK",
    "Action",
    "GameSpec",
    "GameState",
    "get_game",
    "list_games",
    "register_game",
]
