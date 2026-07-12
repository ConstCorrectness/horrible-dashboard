"""Wire models for the game server.

The `/game-ws` protocol uses `{"type": ...}` envelopes (server-to-server style,
like the commons/lobby servers) — distinct from the browser's `{channel,event,
data}` `/ws`. A node's `GameServerClient` bridges between the two.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TableStatus = Literal["open", "playing", "done"]

# Capabilities this server advertises on AUTHED. Nodes (and the panels they relay
# to) feature-detect against this list instead of assuming the deployed server is
# as new as they are — the server always deploys first, older nodes just see caps
# they don't know and ignore them. Grows as features land; never remove an entry
# a shipped node relies on.
SERVER_CAPS: list[str] = [
    "match_info",  # broadcasts seat identities (SeatProfile) at match start
    "trace",  # accepts move_trace uploads into the match replay
    "replays",  # persists replays + serves them over /replays HTTP
    "tiers",  # placement matches + tier names on ladder/profiles, rating_update pushes
    "series",  # best-of-N tables (Ruleset.best_of) with series_state/series_over
    "negotiation",  # challenge_offer/respond handshake + rematch_offer
    "queue",  # ranked matchmaking queue with practice-bot backfill
    "models",  # accepts loadout_meta declarations (model label into replays/badges)
    "work",  # server-side grading (WORK nodes): code golf, test duel, bug hunt
    "task_bank",  # per-match tasks neither player has seen (bug hunt)
    "spectate",  # watch_table: live public_state stream for non-participants
    "fighter",  # the 2D fighter game (tick protocol + arcade mode)
]


class Ruleset(BaseModel):
    """The negotiated terms a table is played under (a bare `create_table` gets the
    defaults: rated Bo1 standard)."""

    game_id: str
    best_of: int = Field(default=1, ge=1, le=9)
    difficulty: str = "standard"
    move_timeout_s: float | None = None
    # Break between series games — the harness-iteration window (0 = a short pause).
    edit_phase_s: int = Field(default=0, ge=0, le=1800)
    # "local" = both sides declare local-only models; enforced node-side, declared
    # in loadout_meta, recorded in the replay for the loser to verify.
    model_class: Literal["any", "local"] = "any"
    rated: bool = True


class SeatProfile(BaseModel):
    """Who is sitting in a seat — broadcast in `match_info` so boards show a real
    opponent (handle/avatar/rating), not a bare piece label."""

    account_id: str
    display_name: str
    handle: str | None = None
    avatar: str = "🙂"
    rating: float | None = None
    tier: str | None = None
    level: int = 1
    is_bot: bool = False
    model_label: str | None = None


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
MOVE_TRACE = "move_trace"  # upload the agent's reasoning steps behind one move
LOADOUT_META = (
    "loadout_meta"  # declare what plays this table: {table_id, version, model_label}
)
REMATCH_OFFER = "rematch_offer"  # offer the last opponent the same ruleset again
CHALLENGE_OFFER = "challenge_offer"  # propose a match: {to_account_id, ruleset}
CHALLENGE_RESPOND = "challenge_respond"  # {offer_id, response: accept|decline|counter}
QUEUE_JOIN = "queue_join"  # enter ranked matchmaking: {game_id, difficulty, placement}
QUEUE_LEAVE = "queue_leave"  # leave the queue
WATCH_TABLE = "watch_table"  # spectate a table (receive its public_state stream)
UNWATCH_TABLE = "unwatch_table"  # stop spectating

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
MATCH_INFO = "match_info"  # match started: seat profiles + the replay id
RATING_UPDATE = "rating_update"  # your post-game rating/tier/xp movement
SERIES_STATE = "series_state"  # between series games: wins so far + intermission
SERIES_OVER = "series_over"  # the series is decided
CHALLENGE_INCOMING = "challenge_incoming"  # someone offered you a match (or counter)
CHALLENGE_UPDATE = "challenge_update"  # your offer's fate: accepted/declined/countered
QUEUE_STATUS = "queue_status"  # periodic while queued: waiting time + search window
MATCH_FOUND = "match_found"  # the queue paired you; the table starts itself
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
