"""Back-compat shim: the duel solver moved to the shared engine so the server's
practice bots can use it too. Import from `backend.games_engine.baseline`."""

from __future__ import annotations

from backend.games_engine.baseline import (  # noqa: F401
    find_open_action,
    solve_answers,
)
