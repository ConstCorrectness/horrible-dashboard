"""Server-hosted practice bots: reference opponents at pinned skill tiers.

They exist so the ladder works solo — placement matches calibrate against them,
the matchmaking queue backfills with them when no human shows up, and the
onboarding wizard's first match is against one. Each bot is a real account
(`bot:{game}:{tier}`, `is_bot=1`) with a **pinned** rating per game
(`store.ensure_bot_account`): beating one moves *your* rating, never theirs.

A bot rides the exact same `/game-ws` protocol as a node — its `_BotConn`
receives what a real socket would and answers `action` messages through
`hub.handle` — so the referee needs zero special cases and stays honest.
Responses are **detached** (`create_task`): the referee prompts seats while
holding its lock, so answering inline would deadlock.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

from backend.games_engine import baseline

if TYPE_CHECKING:
    from backend.games_server.hub import GameHub, Session

logger = logging.getLogger(__name__)

# Pinned ladder anchors, one per tier. Sparse on purpose: practice bots cover the
# climb through gold; above that you play humans.
TIER_RATINGS: dict[str, float] = {
    "bronze": 1000.0,
    "silver": 1150.0,
    "gold": 1300.0,
    "platinum": 1450.0,
}

BOT_NAMES: dict[str, str] = {
    "bronze": "Rusty 🥉",
    "silver": "Circuit 🥈",
    "gold": "Aurum 🥇",
    "platinum": "Nemesis 💠",
}

# How often each tier plays a random legal move instead of its best one.
_BLUNDER_RATE: dict[str, float] = {
    "bronze": 0.5,
    "silver": 0.25,
    "gold": 0.08,
    "platinum": 0.0,
}

# Connect-four search depth per tier.
_C4_DEPTH: dict[str, int] = {"bronze": 1, "silver": 2, "gold": 4, "platinum": 5}


def bot_account_id(game_id: str, tier: str) -> str:
    return f"bot:{game_id}:{tier}"


# ---- per-game move logic -----------------------------------------------------


def _ttt_best_move(board: list[str | None], me: int) -> str:
    """Full minimax on the 3x3 board (marks 'X'/'O'; seat 0 = X)."""
    marks = ("X", "O")

    def winner(b: list[str | None]) -> str | None:
        lines = (
            (0, 1, 2),
            (3, 4, 5),
            (6, 7, 8),
            (0, 3, 6),
            (1, 4, 7),
            (2, 5, 8),
            (0, 4, 8),
            (2, 4, 6),
        )
        for a, bb, c in lines:
            if b[a] is not None and b[a] == b[bb] == b[c]:
                return b[a]
        return None

    def score(b: list[str | None], turn: int) -> int:
        w = winner(b)
        if w is not None:
            return 1 if w == marks[me] else -1
        if all(c is not None for c in b):
            return 0
        best = -2
        for i in range(9):
            if b[i] is None:
                b[i] = marks[turn]
                s = score(b, 1 - turn)
                b[i] = None
                if turn == me:
                    best = max(best if best != -2 else -1, s)
                else:
                    best = min(best if best != -2 else 1, s)
        return best

    best_cell, best_val = None, -2
    for i in range(9):
        if board[i] is None:
            board[i] = marks[me]
            val = score(board, 1 - me)
            board[i] = None
            if val > best_val:
                best_cell, best_val = i, val
    return str(best_cell)


def _c4_best_move(board_top_first: list[list[str | None]], me: int, depth: int) -> str:
    """Depth-limited negamax with a center-preference eval. `board_top_first` is
    the wire format (top row first, marks 'R'/'Y'; seat 0 = R)."""
    marks = ("R", "Y")
    rows, cols = len(board_top_first), len(board_top_first[0])
    # Flip to bottom-first for gravity math.
    grid = [list(board_top_first[rows - 1 - r]) for r in range(rows)]

    def drop_row(col: int) -> int | None:
        for r in range(rows):
            if grid[r][col] is None:
                return r
        return None

    def is_win(mark: str) -> bool:
        for r in range(rows):
            for c in range(cols):
                if grid[r][c] != mark:
                    continue
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    if all(
                        0 <= r + dr * k < rows
                        and 0 <= c + dc * k < cols
                        and grid[r + dr * k][c + dc * k] == mark
                        for k in range(4)
                    ):
                        return True
        return False

    center_pref = [3, 2, 4, 1, 5, 0, 6][:cols]

    def negamax(turn: int, d: int, alpha: float, beta: float) -> float:
        if is_win(marks[1 - turn]):
            return -1000 - d  # the previous mover just won
        if d == 0:
            # Light positional eval: my center presence minus theirs.
            mine = sum(
                (cols // 2) - abs(c - cols // 2)
                for r in range(rows)
                for c in range(cols)
                if grid[r][c] == marks[turn]
            )
            theirs = sum(
                (cols // 2) - abs(c - cols // 2)
                for r in range(rows)
                for c in range(cols)
                if grid[r][c] == marks[1 - turn]
            )
            return float(mine - theirs)
        best = -float("inf")
        moved = False
        for col in center_pref:
            r = drop_row(col)
            if r is None:
                continue
            moved = True
            grid[r][col] = marks[turn]
            val = -negamax(1 - turn, d - 1, -beta, -alpha)
            grid[r][col] = None
            best = max(best, val)
            alpha = max(alpha, val)
            if alpha >= beta:
                break
        return best if moved else 0.0

    best_col, best_val = None, -float("inf")
    for col in center_pref:
        r = drop_row(col)
        if r is None:
            continue
        grid[r][col] = marks[me]
        val = -negamax(1 - me, depth, -float("inf"), float("inf"))
        grid[r][col] = None
        if val > best_val:
            best_col, best_val = col, val
    return str(best_col if best_col is not None else center_pref[0])


def _holdem_move(legal_ids: list[str], tier: str, rng: random.Random) -> str:
    """Cheap tiered poker: bronze is chaotic; higher tiers play tight-passive with
    occasional aggression. Good enough for practice — beating it is the point."""
    if tier == "bronze":
        return rng.choice(legal_ids)
    for preferred in ("check", "call"):
        if preferred in legal_ids:
            if (
                tier in ("gold", "platinum")
                and "raise_min" in legal_ids
                and rng.random() < 0.25
            ):
                return "raise_min"
            return preferred
    return "fold" if "fold" in legal_ids else rng.choice(legal_ids)


def choose_action(
    game_id: str,
    tier: str,
    observation: dict[str, Any],
    legal_actions: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[str, Any]:
    """(action_id, payload) for one turn. Falls back to a random legal move for
    games without dedicated bot logic."""
    ids = [str(a.get("id")) for a in legal_actions]
    open_action = baseline.find_open_action(legal_actions)
    if open_action is not None:
        # Agentic-task games: the shared baseline solver is the bot's brain.
        return str(open_action.get("id")), baseline.solve_open_action(
            open_action, observation
        )
    if rng.random() < _BLUNDER_RATE.get(tier, 0.5):
        return rng.choice(ids), None
    try:
        if game_id == "tictactoe" and isinstance(observation.get("board"), list):
            return _ttt_best_move(
                list(observation["board"]), _seat_of_obs(observation)
            ), None
        if game_id == "connect_four" and isinstance(observation.get("board"), list):
            depth = _C4_DEPTH.get(tier, 2)
            return (
                _c4_best_move(observation["board"], _seat_of_obs(observation), depth),
                None,
            )
        if game_id == "holdem":
            return _holdem_move(ids, tier, rng), None
    except Exception:
        logger.debug("bot move logic failed; playing random", exc_info=True)
    return rng.choice(ids), None


def _seat_of_obs(observation: dict[str, Any]) -> int:
    """Whose turn the observation says it is — for perfect-information games the
    bot to move IS that seat."""
    return int(observation.get("turn") or 0)


# ---- the wire-level bot player -------------------------------------------------


class _BotConn:
    """The bot's end of a fake socket: the hub sends here like any connection."""

    def __init__(self) -> None:
        self.player: BotPlayer | None = None

    async def send_json(self, msg: dict[str, Any]) -> None:
        if self.player is not None:
            self.player.on_message(msg)


class BotPlayer:
    """One seated practice bot: a synthetic authed session + the auto-play loop."""

    def __init__(
        self, hub: "GameHub", game_id: str, tier: str, *, delay_s: float = 0.4
    ) -> None:
        tier = tier if tier in TIER_RATINGS else "bronze"
        self.hub = hub
        self.game_id = game_id
        self.tier = tier
        self.account_id = bot_account_id(game_id, tier)
        self.display_name = BOT_NAMES[tier]
        self._delay_s = delay_s
        self._rng = random.Random()
        conn = _BotConn()
        conn.player = self
        self.session: "Session" = hub.connect(conn)
        # Server-side identity: bots don't hold tokens, the hub vouches for them.
        self.session.account_id = self.account_id
        self.session.display_name = self.display_name
        hub.register_session(self.session)

    def on_message(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "your_turn":
            # Detached: the referee holds its lock while prompting; acting inline
            # would deadlock. The small delay keeps the pacing watchable.
            asyncio.create_task(self._act(msg))

    async def _act(self, turn: dict[str, Any]) -> None:
        try:
            if self._delay_s > 0:
                await asyncio.sleep(self._delay_s + self._rng.random() * self._delay_s)
            legal = turn.get("legal_actions") or []
            if not legal:
                return
            action_id, payload = choose_action(
                str(turn.get("game_id") or self.game_id),
                self.tier,
                turn.get("observation") or {},
                legal,
                self._rng,
            )
            await self.hub.handle(
                self.session,
                {
                    "type": "action",
                    "game_id": turn.get("game_id"),
                    "action_id": action_id,
                    **({"payload": payload} if payload is not None else {}),
                },
            )
        except Exception:
            logger.exception("practice bot failed to act")
