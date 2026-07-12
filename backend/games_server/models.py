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

# node -> server (AgentTown: the persistent social world, not a table)
TOWN_JOIN = "town_join"  # spawn/wake your resident (name + avatar ride along)
TOWN_LEAVE = "town_leave"  # despawn your resident
TOWN_ACT = "town_act"  # queue this tick's action: stay/move/say/emote

# server -> node (AgentTown)
TOWN_JOINED = "town_joined"  # join ack: your resident + the town snapshot
TOWN_STATE = "town_state"  # per-tick broadcast: residents, events, phase
TOWN_TICK = "town_tick"  # your resident's observation — act before next tick

# node -> server (The Plaza: the *human* social layer — real users, not agents)
SOCIAL_JOIN = "social_join"  # enter the plaza (name + avatar ride along)
SOCIAL_LEAVE = "social_leave"  # leave the plaza
SOCIAL_MOVE = "social_move"  # walk to (x, y) in the current room
SOCIAL_ROOM = "social_room"  # switch to another room
SOCIAL_SAY = "social_say"  # speak — a bubble pops over your avatar in the room
SOCIAL_EMOTE = "social_emote"  # a quick emote (👋🎉…) over your avatar
SOCIAL_INVITE = "social_invite"  # challenge another user to a game (hosts a table)
FRIEND_REQUEST = "friend_request"  # ask to be someone's friend
FRIEND_ACCEPT = "friend_accept"  # accept an incoming friend request
FRIEND_REMOVE = "friend_remove"  # remove a friend / decline a request
FRIEND_LIST = "friend_list"  # ask for my friends + pending requests
PROFILE_GET = "profile_get"  # ask for my gamified profile (avatar, xp, level)
PROFILE_SET = "profile_set"  # update my avatar / bio

# server -> node (The Plaza)
SOCIAL_JOINED = "social_joined"  # join ack: you + the current room snapshot
SOCIAL_STATE = "social_state"  # a room's live state: occupants + recent bubbles
SOCIAL_ROSTER = "social_roster"  # who's online across all rooms (+ their activity)
SOCIAL_INVITED = "social_invited"  # someone challenged you: table_id + who + game
FRIENDS = "friends"  # your friends list + incoming pending requests
PROFILE = "profile"  # your gamified profile

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
    payload: Any = Field(
        default=None,
        description=(
            "free-form content for open actions (duel answers, patches); the game "
            "validates its shape server-side"
        ),
    )
