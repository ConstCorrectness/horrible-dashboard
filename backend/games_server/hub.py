"""Connection, session, and table management for the game server.

The hub is transport-agnostic: it talks to a `Conn` (anything with an async
`send_json`), so the FastAPI `/game-ws` endpoint and the in-process integration
tests drive the exact same code. It owns the account->connection map, the lobby of
tables, and dispatch of the `/game-ws` protocol; the per-game rules live in the
`Referee`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.games_engine.base import get_game
from backend.games_server import models
from backend.games_server.matchmaking import Matchmaker
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


@dataclass
class _Offer:
    """A pending challenge (or rematch/counter) awaiting the target's response."""

    id: str
    from_session_id: str
    from_account: str
    from_name: str
    to_account: str
    ruleset: models.Ruleset
    kind: str = "challenge"  # challenge | rematch | counter
    created_at: float = field(default_factory=time.time)


OFFER_TTL_S = 180.0


class _Table:
    def __init__(
        self,
        table_id: str,
        game_id: str,
        capacity: int,
        ruleset: models.Ruleset | None = None,
    ) -> None:
        self.id = table_id
        self.game_id = game_id
        self.capacity = capacity
        # Seats hold **session ids** (the routing identity), not account ids, so the
        # same account can occupy two seats from two devices. `account_of` maps each
        # seated session back to its account for the lobby display and the ladder.
        self.seats: list[str | None] = [None] * capacity
        self.account_of: dict[str, str] = {}
        self.referee: Referee | None = None
        # Minted up front so `match_info` can point players at the replay their
        # game will become the moment it finishes. Re-minted per series game.
        self.replay_id = uuid.uuid4().hex[:12]
        # The negotiated terms; a bare create_table plays rated Bo1 standard.
        self.ruleset = ruleset or models.Ruleset(game_id=game_id)
        # Best-of-N bookkeeping. `wins` is aligned with the *current* seat order
        # (seats swap between series games for first-move fairness; wins swap too).
        self.series_id = uuid.uuid4().hex[:12]
        self.game_index = 0
        self.wins: list[int] = [0] * capacity
        self.series_done = False
        # session_id -> {version, model_label}: what each node declared it plays
        # with (loadout_meta). Recorded into the replay for post-match scrutiny.
        self.meta_of: dict[str, dict[str, Any]] = {}

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
        # Ranked matchmaking (the app lifespan starts its sweep loop).
        self.matchmaker = Matchmaker(self)
        # Pending challenge offers by id (in-memory: both parties must be online).
        self._offers: dict[str, _Offer] = {}

    # ---- connection lifecycle ---------------------------------------------

    def connect(self, conn: Conn) -> Session:
        return Session(conn)

    def register_session(self, session: Session) -> None:
        """Admit a pre-authed session (practice bots — the hub vouches for them)."""
        self._sessions[session.session_id] = session

    async def disconnect(self, session: Session) -> None:
        if self._sessions.get(session.session_id) is session:
            del self._sessions[session.session_id]
        self.matchmaker.on_disconnect(session)
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
        elif mtype == models.MOVE_TRACE:
            await self._move_trace(session, msg)
        elif mtype == models.LOADOUT_META:
            self._loadout_meta(session, msg)
        elif mtype == models.CHALLENGE_OFFER:
            await self._challenge_offer(session, msg)
        elif mtype == models.CHALLENGE_RESPOND:
            await self._challenge_respond(session, msg)
        elif mtype == models.REMATCH_OFFER:
            await self._rematch_offer(session, msg)
        elif mtype == models.QUEUE_JOIN:
            await self._queue_join(session, msg)
        elif mtype == models.QUEUE_LEAVE:
            self.matchmaker.leave(session)
        elif mtype == models.WATCH_TABLE:
            await self._watch_table(session, msg)
        elif mtype == models.UNWATCH_TABLE:
            self._unwatch_table(session, msg)
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
                "caps": models.SERVER_CAPS,
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

    def _host_table(
        self, game_id: str, ruleset: models.Ruleset | None = None
    ) -> _Table:
        spec = get_game(game_id)  # raises KeyError for unknown games
        table = _Table(uuid.uuid4().hex[:8], game_id, spec.max_players, ruleset)
        self._tables[table.id] = table
        return table

    def _parse_ruleset(
        self, game_id: str, msg: dict[str, Any]
    ) -> models.Ruleset | None:
        """The optional negotiated terms riding on create_table/offers. Invalid or
        missing input falls back to the defaults rather than erroring."""
        raw = msg.get("ruleset")
        if not isinstance(raw, dict):
            return None
        try:
            return models.Ruleset(**{**raw, "game_id": game_id})
        except Exception:
            return None

    async def _create_table(self, session: Session, msg: dict[str, Any]) -> None:
        game_id = str(msg.get("game_id") or "")
        try:
            table = self._host_table(game_id, self._parse_ruleset(game_id, msg))
        except KeyError:
            await session.conn.send_json(
                models.error("bad_game", f"unknown game {game_id!r}")
            )
            return
        await self._seat(session, table)

    async def _social_invite(self, session: Session, msg: dict[str, Any]) -> None:
        """Challenge another user (from the Plaza roster/friends) to a game: host a
        table seated by the inviter, then push a `social_invited` to every online
        session of the target so their node can join it with one click."""
        assert session.account_id is not None
        game_id = str(msg.get("game_id") or "")
        target = str(msg.get("account_id") or "")
        try:
            table = self._host_table(game_id)
        except KeyError:
            await session.conn.send_json(
                models.error("bad_game", f"unknown game {game_id!r}")
            )
            return
        await self._seat(session, table)
        invite = {
            "type": models.SOCIAL_INVITED,
            "table_id": table.id,
            "game_id": game_id,
            "game_name": self._game_name(game_id),
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
        # minutes, not seconds). The hub's 0 stays a master "clock off" switch; the
        # negotiated ruleset can override either.
        timeout = self._move_timeout_s
        if timeout > 0 and spec.move_timeout_s is not None:
            timeout = spec.move_timeout_s
        if timeout > 0 and table.ruleset.move_timeout_s:
            timeout = table.ruleset.move_timeout_s
        table.referee = Referee(
            table_id=table.id,
            game_id=table.game_id,
            state=spec.new(**self._state_kwargs(table, accounts)),
            seats=seats,
            accounts=accounts,
            send_to=self._send_to_session,
            rng=self._rng,
            move_timeout_s=timeout,
            on_result=lambda gid, tid, accs, returns, winner: self._on_game_result(
                table, seats, accs, returns, winner
            ),
            on_replay=lambda events, returns, winner: self._save_replay(
                table, accounts, events, returns, winner
            ),
        )
        await self._broadcast_table(table)
        await self._broadcast_match_info(table, seats, accounts)
        await table.referee.start()

    def _state_kwargs(self, table: _Table, accounts: list[str]) -> dict[str, Any]:
        """Per-game construction kwargs. Task-bank games (bug hunt, and later the
        code games) get a task neither player has seen, marked played up front so a
        best-of-N series doesn't repeat it."""
        spec = get_game(table.game_id)
        kind = {"bug_hunt": "bug_hunt"}.get(table.game_id)
        if kind is None or "task" not in _factory_params(spec):
            return {}
        from backend.games_server import task_bank

        try:
            task = task_bank.pick_task(kind, table.ruleset.difficulty, accounts)
            if task is not None:
                task_bank.mark_played(accounts, task["id"])
                return {"task": task}
        except Exception:
            logger.exception("failed to pick a bank task; using the engine default")
        return {}

    async def _broadcast_match_info(
        self, table: _Table, seats: list[str], accounts: list[str]
    ) -> None:
        """Tell every player who they're up against: seat profiles (handle, avatar,
        rating) plus the id the finished game's replay will be saved under."""
        profiles = []
        for seat, account_id in enumerate(accounts):
            profile = self._seat_profile(account_id, table.game_id)
            meta = table.meta_of.get(seats[seat]) if seat < len(seats) else None
            if meta and meta.get("model_label"):
                profile.model_label = meta["model_label"]
            profiles.append(profile)
        info = {
            "type": models.MATCH_INFO,
            "table_id": table.id,
            "game_id": table.game_id,
            "replay_id": table.replay_id,
            "ruleset": table.ruleset.model_dump(),
            "seats": [p.model_dump() for p in profiles],
        }
        for session_id in dict.fromkeys(seats):
            await self._send_to_session(session_id, info)

    def _seat_profile(self, account_id: str, game_id: str) -> models.SeatProfile:
        """Best-effort identity card for one seat; a store hiccup falls back to the
        bare account id rather than blocking the match."""
        from backend.games_server import store

        try:
            profile = store.get_profile(account_id)
            account = store.get_account(account_id)
            rating = store.get_rating(account_id, game_id)
            return models.SeatProfile(
                account_id=account_id,
                display_name=(account or {}).get("display_name") or account_id,
                handle=(account or {}).get("handle"),
                avatar=profile["avatar"],
                rating=round(rating["rating"], 1) if rating else None,
                tier=(
                    store.tier_for(rating["rating"], rating["placement_games"])
                    if rating
                    else None
                ),
                level=profile["level"],
                is_bot=bool((account or {}).get("is_bot")),
            )
        except Exception:
            logger.exception("failed to build seat profile for %s", account_id)
            return models.SeatProfile(account_id=account_id, display_name=account_id)

    def _save_replay(
        self,
        table: _Table,
        accounts: list[str],
        events: list[dict[str, Any]],
        returns: dict[int, float],
        winner: int | None,
    ) -> None:
        """Persist a finished game's replay. Guarded like `_record_result`: a store
        failure never crashes the table."""
        from backend.games_server import store

        models_used = {
            seat: meta["model_label"]
            for seat, session_id in enumerate(s for s in table.seats if s is not None)
            if (meta := table.meta_of.get(session_id)) and meta.get("model_label")
        }
        try:
            store.save_replay(
                replay_id=table.replay_id,
                game_id=table.game_id,
                table_id=table.id,
                seats=accounts,
                events=events,
                winner=winner,
                returns=returns,
                series_id=table.series_id if table.ruleset.best_of > 1 else None,
                ruleset=table.ruleset.model_dump(),
                models_used=models_used or None,
            )
        except Exception:
            logger.exception("failed to save replay")

    def _loadout_meta(self, session: Session, msg: dict[str, Any]) -> None:
        """A node declaring what plays at its seat (harness version + model label).
        Declaration, not proof — it lands in the replay so the loser can check."""
        table = self._tables.get(str(msg.get("table_id") or ""))
        if table is None or session.session_id not in table.seats:
            return
        table.meta_of[session.session_id] = {
            "version": str(msg.get("version") or "") or None,
            "model_label": str(msg.get("model_label") or "") or None,
        }

    async def _watch_table(self, session: Session, msg: dict[str, Any]) -> None:
        """Spectate a live table: join its referee's observer set and get a
        snapshot of the current public state right away."""
        table = self._tables.get(str(msg.get("table_id") or ""))
        if table is None or table.referee is None:
            await session.conn.send_json(models.error("no_table", "table not found"))
            return
        table.referee.observers.add(session.session_id)
        await session.conn.send_json(
            {
                "type": models.PUBLIC_STATE,
                "game_id": table.game_id,
                "table_id": table.id,
                "state": table.referee.state.public_state(),
            }
        )

    def _unwatch_table(self, session: Session, msg: dict[str, Any]) -> None:
        table = self._tables.get(str(msg.get("table_id") or ""))
        if table is not None and table.referee is not None:
            table.referee.observers.discard(session.session_id)

    async def _move_trace(self, session: Session, msg: dict[str, Any]) -> None:
        """A node uploading the reasoning behind one of its moves. Stored into the
        table's replay via the referee; never rebroadcast (post-match reveal only)."""
        table = _table_of_session(self._tables, session.session_id)
        if table is None or table.referee is None:
            return
        seat = table.referee._seat_of(session.session_id)
        if seat is None:
            return
        table.referee.record_trace(
            seat,
            msg.get("steps") or [],
            str(msg.get("action_id")) if msg.get("action_id") else None,
        )

    def _on_game_result(
        self,
        table: _Table,
        seat_sessions: list[str],
        accounts: list[str],
        returns: dict[int, float],
        winner: int | None,
    ) -> None:
        """One game of a table finished: record it (ladder + XP), push each seat its
        rating movement, and either continue the series or wrap it up. Sync (the
        referee's `on_result` contract); the async follow-ups run detached. Guarded
        so a store hiccup never crashes a table."""
        from backend.games_server import store

        try:
            updates = store.record_result(
                table.game_id,
                table.id,
                accounts,
                returns,
                winner,
                rated=table.ruleset.rated,
                series_id=table.series_id if table.ruleset.best_of > 1 else None,
                ruleset=table.ruleset.model_dump(),
            )
        except Exception:
            logger.exception("failed to record game result")
            updates = []
        for update in updates:
            seat = int(update.get("seat", -1))
            if 0 <= seat < len(seat_sessions):
                self._fire(
                    self._send_to_session(
                        seat_sessions[seat],
                        {"type": models.RATING_UPDATE, **update},
                    )
                )

        # ---- series bookkeeping ----
        if winner is not None and 0 <= winner < len(table.wins):
            table.wins[winner] += 1
        table.game_index += 1
        needed = table.ruleset.best_of // 2 + 1
        decided = max(table.wins) >= needed or table.game_index >= table.ruleset.best_of
        if not decided:
            self._fire(self._continue_series(table))
            return
        table.series_done = True
        if table.ruleset.best_of > 1:
            series_winner = (
                table.wins.index(max(table.wins))
                if table.wins.count(max(table.wins)) == 1
                else None
            )
            try:
                store.record_series(
                    table.series_id,
                    table.game_id,
                    table.ruleset.best_of,
                    accounts,
                    table.wins,
                    series_winner,
                    ruleset=table.ruleset.model_dump(),
                )
            except Exception:
                logger.exception("failed to record series")
            self._fire(
                self._broadcast_to_seats(
                    table,
                    {
                        "type": models.SERIES_OVER,
                        "table_id": table.id,
                        "series_id": table.series_id,
                        "best_of": table.ruleset.best_of,
                        "wins": table.wins,
                        "winner_seat": series_winner,
                        "seats": accounts,
                    },
                )
            )
        self._release_bots(table)
        # Back to the lobby in the Plaza roster. This also nudges each player's
        # profile to refresh (XP just changed).
        for account_id in accounts:
            self._set_activity(account_id, "In the lobby")

    def _fire(self, coro: Any) -> None:
        """Run an async follow-up detached from the referee's lock."""
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()

    async def _continue_series(self, table: _Table) -> None:
        """Between series games: announce the score, hold the harness-iteration
        window open, swap seats for first-move fairness, and start the next game."""
        seats = [s for s in table.seats if s is not None]
        accounts = [table.account_of[s] for s in seats]
        intermission = max(1, table.ruleset.edit_phase_s)
        await self._broadcast_to_seats(
            table,
            {
                "type": models.SERIES_STATE,
                "table_id": table.id,
                "series_id": table.series_id,
                "best_of": table.ruleset.best_of,
                "game_index": table.game_index,
                "wins": table.wins,
                "seats": accounts,
                "intermission_s": intermission,
            },
        )
        await asyncio.sleep(intermission)
        table.seats.reverse()
        table.wins.reverse()
        table.replay_id = uuid.uuid4().hex[:12]
        await self._start_table(table)

    async def _broadcast_to_seats(self, table: _Table, msg: dict[str, Any]) -> None:
        for session_id in dict.fromkeys(s for s in table.seats if s is not None):
            await self._send_to_session(session_id, msg)

    def _release_bots(self, table: _Table) -> None:
        """Drop the synthetic sessions of any practice bots seated at this table."""
        for session_id in table.seats:
            if session_id is None:
                continue
            session = self._sessions.get(session_id)
            if session and (session.account_id or "").startswith("bot:"):
                del self._sessions[session_id]

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

    # ---- challenge negotiation ----------------------------------------------

    def _prune_offers(self) -> None:
        cutoff = time.time() - OFFER_TTL_S
        for offer_id in [
            oid for oid, o in self._offers.items() if o.created_at < cutoff
        ]:
            del self._offers[offer_id]

    async def _push_to_account(self, account_id: str, msg: dict[str, Any]) -> int:
        """Send to every online session of an account; returns how many got it."""
        count = 0
        for session in list(self._sessions.values()):
            if session.account_id == account_id:
                await self._send_to_session(session.session_id, msg)
                count += 1
        return count

    async def _make_offer(
        self,
        session: Session,
        to_account: str,
        ruleset: models.Ruleset,
        kind: str,
    ) -> None:
        assert session.account_id is not None
        self._prune_offers()
        offer = _Offer(
            id=uuid.uuid4().hex[:8],
            from_session_id=session.session_id,
            from_account=session.account_id,
            from_name=session.display_name or session.account_id,
            to_account=to_account,
            ruleset=ruleset,
            kind=kind,
        )
        self._offers[offer.id] = offer
        delivered = await self._push_to_account(
            to_account,
            {
                "type": models.CHALLENGE_INCOMING,
                "offer_id": offer.id,
                "kind": kind,
                "from_id": offer.from_account,
                "from_name": offer.from_name,
                "game_name": self._game_name(ruleset.game_id),
                "ruleset": ruleset.model_dump(),
            },
        )
        if delivered == 0:
            del self._offers[offer.id]
            await session.conn.send_json(
                models.error("offline", f"{to_account} is not online")
            )

    async def _challenge_offer(self, session: Session, msg: dict[str, Any]) -> None:
        """Propose a match to a specific player: game + full ruleset (best-of, time
        controls, difficulty, model class, rated). They accept, decline, or counter."""
        game_id = str(
            (msg.get("ruleset") or {}).get("game_id") or msg.get("game_id") or ""
        )
        try:
            get_game(game_id)
        except KeyError:
            await session.conn.send_json(
                models.error("bad_game", f"unknown game {game_id!r}")
            )
            return
        ruleset = self._parse_ruleset(game_id, msg) or models.Ruleset(game_id=game_id)
        await self._make_offer(
            session, str(msg.get("to_account_id") or ""), ruleset, "challenge"
        )

    async def _challenge_respond(self, session: Session, msg: dict[str, Any]) -> None:
        assert session.account_id is not None
        self._prune_offers()
        offer = self._offers.pop(str(msg.get("offer_id") or ""), None)
        if offer is None:
            await session.conn.send_json(
                models.error("no_offer", "offer expired or already answered")
            )
            return
        if offer.to_account != session.account_id:
            await session.conn.send_json(
                models.error("not_yours", "that offer is not addressed to you")
            )
            return
        response = str(msg.get("response") or "")
        update: dict[str, Any] = {
            "type": models.CHALLENGE_UPDATE,
            "offer_id": offer.id,
            "by_name": session.display_name or session.account_id,
        }
        if response == "accept":
            table = self._host_table(offer.ruleset.game_id, offer.ruleset)
            await self._seat(session, table)
            # The offerer's node auto-joins on this push (client.py).
            await self._send_to_session(
                offer.from_session_id,
                {
                    **update,
                    "status": "accepted",
                    "table_id": table.id,
                    "ruleset": offer.ruleset.model_dump(),
                },
            )
        elif response == "counter":
            # Roles flip: the counter is a fresh offer back at the original offerer.
            countered = self._parse_ruleset(offer.ruleset.game_id, msg) or offer.ruleset
            await self._send_to_session(
                offer.from_session_id, {**update, "status": "countered"}
            )
            await self._make_offer(session, offer.from_account, countered, "counter")
        else:
            await self._send_to_session(
                offer.from_session_id, {**update, "status": "declined"}
            )

    async def _rematch_offer(self, session: Session, msg: dict[str, Any]) -> None:
        """Offer the opponent of a finished table the same ruleset again."""
        assert session.account_id is not None
        table = self._tables.get(str(msg.get("table_id") or ""))
        if table is None or session.session_id not in table.seats:
            await session.conn.send_json(models.error("no_table", "table not found"))
            return
        others = [
            table.account_of[s]
            for s in table.seats
            if s is not None and s != session.session_id
        ]
        if not others:
            return
        await self._make_offer(session, others[0], table.ruleset, "rematch")

    # ---- ranked queue --------------------------------------------------------

    async def _queue_join(self, session: Session, msg: dict[str, Any]) -> None:
        game_id = str(msg.get("game_id") or "")
        try:
            get_game(game_id)
        except KeyError:
            await session.conn.send_json(
                models.error("bad_game", f"unknown game {game_id!r}")
            )
            return
        error = await self.matchmaker.join(
            session,
            game_id,
            difficulty=str(msg.get("difficulty") or "standard"),
            placement=bool(msg.get("placement")),
        )
        if error is not None:
            await session.conn.send_json(models.error(error["code"], error["message"]))

    async def start_queue_match(
        self,
        game_id: str,
        difficulty: str,
        session_a: Session,
        session_b: Session | None,
        bot_tier: str | None = None,
    ) -> None:
        """The matchmaker found a pairing: host a rated table and seat both sides
        (a practice bot fills seat B on backfill)."""
        ruleset = models.Ruleset(game_id=game_id, difficulty=difficulty, rated=True)
        table = self._host_table(game_id, ruleset)
        humans = [s for s in (session_a, session_b) if s is not None]
        opponent_of = {}
        if session_b is not None:
            opponent_of = {
                session_a.session_id: session_b.account_id,
                session_b.session_id: session_a.account_id,
            }
        for session in humans:
            opponent = opponent_of.get(session.session_id)
            await self._send_to_session(
                session.session_id,
                {
                    "type": models.MATCH_FOUND,
                    "table_id": table.id,
                    "ruleset": ruleset.model_dump(),
                    "opponent": (
                        self._seat_profile(opponent, game_id).model_dump()
                        if opponent
                        else None
                    ),
                },
            )
        await self._seat(session_a, table)
        if session_b is not None:
            await self._seat(session_b, table)
        elif bot_tier is not None:
            await self.seat_bot(table, bot_tier)

    async def seat_bot(self, table: _Table, tier: str, *, delay_s: float = 0.4) -> None:
        """Seat a server-hosted practice bot at `table` (creating its pinned
        account on first use)."""
        from backend.games_server import bots, store

        bot = bots.BotPlayer(self, table.game_id, tier, delay_s=delay_s)
        try:
            store.ensure_bot_account(
                bot.account_id,
                bot.display_name,
                table.game_id,
                bots.TIER_RATINGS[bot.tier],
            )
        except Exception:
            logger.exception("failed to ensure bot account")
        await self._seat(bot.session, table)

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


def _factory_params(spec: Any) -> set[str]:
    """The parameter names a game's factory accepts (so the hub only passes kwargs
    the engine actually takes)."""
    import inspect

    try:
        return set(inspect.signature(spec.factory).parameters)
    except (TypeError, ValueError):
        return set()


def _table_of_session(tables: dict[str, _Table], session_id: str) -> _Table | None:
    for table in tables.values():
        if session_id in table.seats and table.referee is not None:
            return table
    return None
