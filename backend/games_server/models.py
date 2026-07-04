"""Wire models for the game server.

The `/game-ws` protocol uses `{"type": ...}` envelopes (server-to-server style,
like the commons/lobby servers) — distinct from the browser's `{channel,event,
data}` `/ws`. A node's `GameServerClient` bridges between the two.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TableStatus = Literal["open", "playing", "done"]


class TableInfo(BaseModel):
    """A table as advertised in the lobby. `seats` holds account ids (or null for
    an empty seat); observers never appear here."""

    id: str
    game_id: str
    status: TableStatus
    seats: list[str | None]
    capacity: int


class GameOverInfo(BaseModel):
    game_id: str
    table_id: str
    returns: dict[int, float]
    winner: int | None = None


# ---- message type constants (server <-> node) ------------------------------

# node -> server
AUTH = "auth"
LIST_TABLES = "list_tables"
CREATE_TABLE = "create_table"
JOIN_TABLE = "join_table"
LEAVE_TABLE = "leave_table"
ACTION = "action"
CHALLENGE_START = "challenge_start"  # ask for a game's challenge scenarios
CHALLENGE_ANSWERS = "challenge_answers"  # submit chosen actions for grading

# server -> node
AUTHED = "authed"
TABLES = "tables"
TABLE = "table"
YOUR_TURN = "your_turn"
PUBLIC_STATE = "public_state"
GAME_OVER = "game_over"
ERROR = "error"
CHALLENGE_SCENARIOS = (
    "challenge_scenarios"  # positions (no solutions) to run the harness on
)
CHALLENGE_REPORT = "challenge_report"  # graded result + whether it's a new best


def error(code: str, message: str) -> dict[str, Any]:
    return {"type": ERROR, "code": code, "message": message}


class WireError(BaseModel):
    """Structured error, mirrored by `error()` for convenience."""

    code: str
    message: str = ""


class ActionMsg(BaseModel):
    """A node's chosen move — validated by the referee against `legal_actions`."""

    game_id: str
    action_id: str
    amount: float | None = Field(
        default=None, description="raise size for betting games"
    )
