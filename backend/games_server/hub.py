"""Connection, session, and table management for the game server.

The hub is transport-agnostic: it talks to a `Conn` (anything with an async
`send_json`), so the FastAPI `/game-ws` endpoint and the in-process integration
tests drive the exact same code. It owns the account->connection map, the lobby of
tables, and dispatch of the `/game-ws` protocol; the per-game rules live in the
`Referee`.
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import Any, Protocol

from backend.games_engine.base import get_game
from backend.games_server import models
from backend.games_server.referee import DEFAULT_MOVE_TIMEOUT_S, Referee
from backend.games_server.social import SocialHub
from backend.games_server.town import TownHub

# The Plaza (human social layer) + friends + profile message types the hub routes
# straight to the SocialHub. SOCIAL_INVITE is the exception — it hosts a table, so
# the hub handles it itself (see `_social_invite`).
_SOCIAL_TYPES = frozenset(
    {
        models.SOCIAL_JOIN,
        models.SOCIAL_LEAVE,
        models.SOCIAL_MOVE,
        models.SOCIAL_ROOM,
        models.SOCIAL_SAY,
        models.SOCIAL_EMOTE,
        models.FRIEND_REQUEST,
        models.FRIEND_ACCEPT,
        models.FRIEND_REMOVE,
        models.FRIEND_LIST,
        models.PROFILE_GET,
        models.PROFILE_SET,
    }
)

logger = logging.getLogger(__name__)


class Conn(Protocol):
    async def send_json(self, msg: dict[str, Any]) -> None: ...


class Session:
    """One `/game-ws` connection.

    Each connection gets its own `session_id` — the routing/seat identity — so a
    single account signed in from **two devices** is two distinct players (the hub
    keys everything by `session_id`, never `account_id`). The `account_id` stays on
    the session for identity: it's what the lobby advertises and what the ladder
    records. Authed sessions carry both.
    """

    def __init__(self, conn: Conn) -> None:
        self.conn = conn
        self.session_id = uuid.uuid4().hex
        self.account_id: str | None = None
        self.display_name: str | None = None


class _Table:
    def __init__(self, table_id: str, game_id: str, capacity: int) -> None:
        self.id = table_id
        self.game_id = game_id
        self.capacity = capacity
        # Seats hold **session ids** (the routing identity), not account ids, so the
        # same account can occupy two seats from two devices. `account_of` maps each
        # seated session back to its account for the lobby display and the ladder.
        self.seats: list[str | None] = [None] * capacity
        self.account_of: dict[str, str] = {}
        self.referee: Referee | None = None

    @property
    def status(self) -> models.TableStatus:
        if self.referee is not None:
            return "done" if self.referee.done else "playing"
        return "open"

    def info(self) -> models.TableInfo:
        # Advertise account ids (per the wire contract), resolving each seated
        # session id back to its account.
        return models.TableInfo(
            id=self.id,
            game_id=self.game_id,
            status=self.status,
            seats=[
                self.account_of.get(s) if s is not None else None for s in self.seats
            ],
            capacity=self.capacity,
        )


class GameHub:
    def __init__(
        self,
        *,
        rng: random.Random | None = None,
        move_timeout_s: float = DEFAULT_MOVE_TIMEOUT_S,
    ) -> None:
        self._rng = rng or random.Random()
        self._move_timeout_s = move_timeout_s
        # session_id -> live session. Keyed per-connection (not per-account) so two
        # devices signed in to the same account are two independent players.
        self._sessions: dict[str, Session] = {}
        self._tables: dict[str, _Table] = {}
        # AgentTown: the persistent social world (identity is per-account there —
        # one resident per account; see town.py). The app lifespan starts its clock.
        self.town = TownHub()
        # The Plaza: the human social layer — presence, rooms, friends (see social.py).
        self.social = SocialHub()

    # ---- connection lifecycle ---------------------------------------------

    def connect(self, conn: Conn) -> Session:
        return Session(conn)

    async def disconnect(self, session: Session) -> None:
        if self._sessions.get(session.session_id) is session:
            del self._sessions[session.session_id]
        # The town keeps the fish: its resident falls asleep instead of vanishing.
        self.town.on_disconnect(session)
        # The Plaza just drops the presence — a human who left has left.
        self.social.on_disconnect(session)

    # ---- dispatch ----------------------------------------------------------

    async def handle(self, session: Session, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == models.AUTH:
            await self._auth(session, msg)
            return
        if session.account_id is None:
            await session.conn.send_json(models.error("unauthed", "authenticate first"))
            return
        if mtype in (models.TOWN_JOIN, models.TOWN_LEAVE, models.TOWN_ACT):
            await self.town.handle(session, msg)
            return
        if mtype in _SOCIAL_TYPES:
            await self.social.handle(session, msg)
            return
        if mtype == models.SOCIAL_INVITE:
            await self._social_invite(session, msg)
            return
        if mtype == models.LIST_TABLES:
            await self._send_tables(session)
        elif mtype == models.CREATE_TABLE:
            await self._create_table(session, msg)
        elif mtype == models.JOIN_TABLE:
            await self._join_table(session, msg)
        elif mtype == models.LEAVE_TABLE:
            await self._leave_table(session, msg)
        elif mtype == models.ACTION:
            await self._action(session, msg)
        elif mtype == models.CHALLENGE_START:
            await self._challenge_start(session, msg)
        elif mtype == models.CHALLENGE_ANSWERS:
            await self._challenge_answers(session, msg)
        else:
            await session.conn.send_json(
                models.error("bad_type", f"unknown message type {mtype!r}")
            )

    # ---- auth (JWT session, or dev token == account id) -------------------

    async def _auth(self, session: Session, msg: dict[str, Any]) -> None:
        from backend.games_server import auth

        resolved = auth.resolve_token(str(msg.get("token") or ""))
        if resolved is None:
            await session.conn.send_json(models.error("auth", "invalid token"))
            return
        session.account_id = resolved["account_id"]
        session.display_name = resolved["display_name"]
        self._sessions[session.session_id] = session
        await session.conn.send_json(
            {
                "type": models.AUTHED,
                "account_id": resolved["account_id"],
                "name": resolved["display_name"],
            }
        )

    # ---- lobby -------------------------------------------------------------

    async def _send_tables(self, session: Session) -> None:
        await session.conn.send_json(
            {
                "type": models.TABLES,
                "tables": [t.info().model_dump() for t in self._tables.values()],
            }
        )

    async def _create_table(self, session: Session, msg: dict[str, Any]) -> None:
        game_id = str(msg.get("game_id") or "")
        try:
            spec = get_game(game_id)
        except KeyError:
            await session.conn.send_json(
                models.error("bad_game", f"unknown game {game_id!r}")
            )
            return
        table = _Table(uuid.uuid4().hex[:8], game_id, spec.max_players)
        self._tables[table.id] = table
        await self._seat(session, table)

    async def _social_invite(self, session: Session, msg: dict[str, Any]) -> None:
        """Challenge another user (from the Plaza roster/friends) to a game: host a
        table seated by the inviter, then push a `social_invited` to every online
        session of the target so their node can join it with one click."""
        assert session.account_id is not None
        game_id = str(msg.get("game_id") or "")
        target = str(msg.get("account_id") or "")
        try:
            spec = get_game(game_id)
        except KeyError:
            await session.conn.send_json(
                models.error("bad_game", f"unknown game {game_id!r}")
            )
            return
        table = _Table(uuid.uuid4().hex[:8], game_id, spec.max_players)
        self._tables[table.id] = table
        await self._seat(session, table)
        invite = {
            "type": models.SOCIAL_INVITED,
            "table_id": table.id,
            "game_id": game_id,
            "game_name": spec.name,
            "from_id": session.account_id,
            "from_name": session.display_name or session.account_id,
        }
        for other in self._sessions.values():
            if other.account_id == target and other is not session:
                await other.conn.send_json(invite)

    def _game_name(self, game_id: str) -> str:
        try:
            return get_game(game_id).name
        except KeyError:
            return game_id

    def _set_activity(self, account_id: str, text: str) -> None:
        """Update a player's Plaza roster status (fire-and-forget; safe from both
        sync and async call sites)."""
        try:
            loop = __import__("asyncio").get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.social.set_activity(account_id, text))

    async def _join_table(self, session: Session, msg: dict[str, Any]) -> None:
        table = self._tables.get(str(msg.get("table_id") or ""))
        if table is None:
            await session.conn.send_json(models.error("no_table", "table not found"))
            return
        if table.status != "open":
            await session.conn.send_json(models.error("closed", "table is not open"))
            return
        await self._seat(session, table)

    async def _seat(self, session: Session, table: _Table) -> None:
        assert session.account_id is not None
        if session.session_id in table.seats:
            await self._broadcast_table(table)
            return
        try:
            idx = table.seats.index(None)
        except ValueError:
            await session.conn.send_json(models.error("full", "table is full"))
            return
        table.seats[idx] = session.session_id
        table.account_of[session.session_id] = session.account_id
        self._set_activity(
            session.account_id, f"At a {self._game_name(table.game_id)} table"
        )
        await self._broadcast_table(table)
        if all(s is not None for s in table.seats):
            await self._start_table(table)

    async def _leave_table(self, session: Session, msg: dict[str, Any]) -> None:
        table = self._tables.get(str(msg.get("table_id") or ""))
        if table is None or session.account_id is None:
            return
        if session.session_id in table.seats and table.status == "open":
            table.seats[table.seats.index(session.session_id)] = None
            table.account_of.pop(session.session_id, None)
            await self._broadcast_table(table)

    async def _start_table(self, table: _Table) -> None:
        spec = get_game(table.game_id)
        # Referee routes by session id (`seats`) but records results by account id
        # (`accounts`) — parallel lists, one entry per filled seat in seat order.
        seats = [s for s in table.seats if s is not None]
        accounts = [table.account_of[s] for s in seats]
        match_status = f"In a {self._game_name(table.game_id)} match"
        for account_id in accounts:
            self._set_activity(account_id, match_status)
        # A game may need a longer move clock than the hub default (duel turns run
        # minutes, not seconds). The hub's 0 stays a master "clock off" switch.
        timeout = self._move_timeout_s
        if timeout > 0 and spec.move_timeout_s is not None:
            timeout = spec.move_timeout_s
        table.referee = Referee(
            table_id=table.id,
            game_id=table.game_id,
            state=spec.new(),
            seats=seats,
            accounts=accounts,
            send_to=self._send_to_session,
            rng=self._rng,
            move_timeout_s=timeout,
            on_result=self._record_result,
        )
        await self._broadcast_table(table)
        await table.referee.start()

    def _record_result(
        self,
        game_id: str,
        table_id: str,
        seats: list[str],
        returns: dict[int, float],
        winner: int | None,
    ) -> None:
        """Persist a finished game to the ladder. Guarded so a store hiccup never
        crashes a table."""
        from backend.games_server import store

        try:
            store.record_result(game_id, table_id, seats, returns, winner)
        except Exception:
            logger.exception("failed to record game result")
        # Back to the lobby in the Plaza roster (seats here are account ids). This
        # also nudges each player's profile to refresh (XP just changed).
        for account_id in seats:
            self._set_activity(account_id, "In the lobby")

    async def _action(self, session: Session, msg: dict[str, Any]) -> None:
        assert session.account_id is not None
        table = _table_of_session(self._tables, session.session_id)
        if table is None or table.referee is None:
            await session.conn.send_json(
                models.error("no_game", "you are not in a live game")
            )
            return
        action_id = str(msg.get("action_id") or "")
        await table.referee.on_action(
            session.session_id, action_id, payload=msg.get("payload")
        )

    # ---- challenge track ---------------------------------------------------

    async def _challenge_start(self, session: Session, msg: dict[str, Any]) -> None:
        """Send the game's scenarios — positions only, never the solutions."""
        from backend.games_server import challenges

        game_id = str(msg.get("game_id") or "")
        run_id = uuid.uuid4().hex[:8]
        await session.conn.send_json(
            {
                "type": models.CHALLENGE_SCENARIOS,
                "run_id": run_id,
                "game_id": game_id,
                "scenarios": challenges.scenarios_for(game_id),
            }
        )

    async def _challenge_answers(self, session: Session, msg: dict[str, Any]) -> None:
        """Grade the node's chosen actions against the hidden solutions and record
        the player's best score."""
        from backend.games_server import challenges, store

        assert session.account_id is not None
        game_id = str(msg.get("game_id") or "")
        answers = {str(k): str(v) for k, v in (msg.get("answers") or {}).items()}
        report = challenges.grade(game_id, answers)
        try:
            is_best = store.record_challenge(session.account_id, game_id, report)
        except Exception:
            logger.exception("failed to record challenge score")
            is_best = False
        await session.conn.send_json(
            {
                "type": models.CHALLENGE_REPORT,
                "run_id": msg.get("run_id"),
                "game_id": game_id,
                "best": is_best,
                **report,
            }
        )

    # ---- sending -----------------------------------------------------------

    async def _send_to_session(self, session_id: str, msg: dict[str, Any]) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return  # offline; the move clock keeps the game moving
        try:
            await session.conn.send_json(msg)
        except Exception:
            logger.debug("failed to send to %s", session_id, exc_info=True)

    async def _broadcast_table(self, table: _Table) -> None:
        payload = {"type": models.TABLE, "table": table.info().model_dump()}
        for session_id in dict.fromkeys(s for s in table.seats if s is not None):
            await self._send_to_session(session_id, payload)


def _table_of_session(tables: dict[str, _Table], session_id: str) -> _Table | None:
    for table in tables.values():
        if session_id in table.seats and table.referee is not None:
            return table
    return None
