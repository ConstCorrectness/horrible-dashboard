"""Node-side client to the central game server.

Owns the node's connection(s) to `/game-ws`, auto-plays each `your_turn` with the
configured `Policy`, and fans server events out to the browser over the local `/ws`
`games` channel so board panels can render live.

The human doesn't move pieces — they *watch their agent play*. For a single-node
demo there's a **self-play** mode that opens a second "sparring" seat from the same
node (keeping the server honest: it still just referees two independent players).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from backend.games_server import models
from backend.modules.games.policy import AgentPolicy, Policy, make_policy
from backend.modules.games.town_policy import TownPolicy
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

# Shipped default: the hosted central game server, so a fresh/packaged node connects
# out of the box. Local dev overrides it via the GAMES_SERVER_URL env (scripts/dev.mjs
# sets ws://localhost:9200 to use the bundled server), and a user can override per-node
# with the games.serverUrl setting.
DEFAULT_SERVER_URL = (
    os.environ.get("GAMES_SERVER_URL") or "wss://horrible-games.fly.dev"
)


def _dev_token() -> str:
    return str(get_value("games.devToken", "player") or "player")


def _settings() -> tuple[str, str, str]:
    """(server url, auth token, policy). The token is the signed-in JWT if we have
    one, else the dev token."""
    url = str(get_value("games.serverUrl", DEFAULT_SERVER_URL) or DEFAULT_SERVER_URL)
    # Imported lazily to avoid a circular import (server_auth imports this module).
    from backend.modules.games.server_auth import get_token

    token = get_token() or _dev_token()
    policy = str(get_value("games.policy", "random") or "random")
    return url, token, policy


class _PlayerConn:
    """One authenticated seat: a socket to the server plus an auto-play loop."""

    def __init__(
        self,
        url: str,
        token: str,
        policy: Policy | None,
        on_event: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._url = url
        self._token = token
        # policy None => manual: don't auto-play; the agent tool / UI drives instead.
        self._policy = policy
        self._on_event = on_event
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self.account_id: str | None = None
        self.authed = asyncio.Event()
        self.last_turn: dict[str, Any] | None = None
        # The AgentTown resident's mind (created on first use; whispers land here).
        self._town_policy: TownPolicy | None = None

    async def start(self) -> None:
        self._ws = await ws_connect(f"{self._url}/game-ws")
        await self._send({"type": models.AUTH, "token": self._token})
        self._task = asyncio.create_task(self._read_loop())
        await asyncio.wait_for(self.authed.wait(), timeout=10.0)

    async def _send(self, msg: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg))

    # Public lobby ops (used by the client on the primary seat).
    async def list_tables(self) -> None:
        await self._send({"type": models.LIST_TABLES})

    async def create_table(self, game_id: str) -> None:
        await self._send({"type": models.CREATE_TABLE, "game_id": game_id})

    async def join_table(self, table_id: str) -> None:
        await self._send({"type": models.JOIN_TABLE, "table_id": table_id})

    async def leave_table(self, table_id: str) -> None:
        await self._send({"type": models.LEAVE_TABLE, "table_id": table_id})

    async def run_challenges(self, game_id: str) -> None:
        await self._send({"type": models.CHALLENGE_START, "game_id": game_id})

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                await self._dispatch(msg)
        except ConnectionClosed:
            pass
        except Exception:
            logger.exception("games player read loop error")

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == models.AUTHED:
            self.account_id = str(msg.get("account_id") or "")
            self.authed.set()
        elif mtype == models.YOUR_TURN:
            self.last_turn = msg
            if self._policy is not None:
                await self._play(msg)
        elif mtype in (models.GAME_OVER, models.PUBLIC_STATE):
            self.last_turn = None  # our turn (if any) is resolved
        elif mtype == models.CHALLENGE_SCENARIOS:
            await self._run_challenge_scenarios(msg)
        elif mtype == models.TOWN_TICK:
            # Detached: agent mode makes a model call that can take seconds, and
            # awaiting it here would stall the read loop (and every other event).
            asyncio.create_task(self._town_act(msg))
        # Relay every server event to the browser (board render, thinking, etc.).
        await _maybe_await(self._on_event(msg))

    async def submit(self, action_id: str, payload: Any = None) -> dict[str, Any]:
        """Manual move path: submit `action_id` for our pending turn (used by the
        `game.chooseAction` agent tool). `payload` carries an open action's content
        (duel answers). Returns an error dict if it's not our turn."""
        turn = self.last_turn
        if turn is None:
            return {"error": "no pending turn"}
        legal = {str(a.get("id")) for a in (turn.get("legal_actions") or [])}
        if action_id not in legal:
            return {"error": f"illegal action {action_id!r}", "legal": sorted(legal)}
        await self._send(
            {
                "type": models.ACTION,
                "game_id": turn.get("game_id"),
                "action_id": action_id,
                **({"payload": payload} if payload is not None else {}),
            }
        )
        self.last_turn = None
        return {"ok": True, "action_id": action_id}

    async def _run_challenge_scenarios(self, msg: dict[str, Any]) -> None:
        """Run our harness/policy over each scenario and submit the chosen actions.

        The scenarios carry no solution — the server grades. A `manual` seat has no
        policy, so we fall back to random just for the challenge run."""
        policy = self._policy or make_policy("random")
        game_id = msg.get("game_id")
        answers: dict[str, str] = {}
        for sc in msg.get("scenarios") or []:
            legal = sc.get("legal_actions") or []
            if not legal:
                continue
            try:
                answers[str(sc.get("id"))] = await policy.choose(
                    sc.get("observation") or {}, legal, game_id
                )
            except Exception:
                logger.debug("challenge scenario failed", exc_info=True)
        await self._send(
            {
                "type": models.CHALLENGE_ANSWERS,
                "run_id": msg.get("run_id"),
                "game_id": game_id,
                "answers": answers,
            }
        )

    async def _play(self, msg: dict[str, Any]) -> None:
        legal = msg.get("legal_actions") or []
        if not legal:
            return
        observation = msg.get("observation") or {}

        # Open actions (duels): the turn wants *content*, not a pick — run the
        # built-in baseline solver so auto-play always finishes the race. Harness
        # skill drives through the manual path (`game.chooseAction` + payload).
        from backend.modules.games.duel_solver import find_open_action, solve_answers

        open_action = find_open_action(legal)
        payload: Any = None
        if open_action is not None:
            action_id = str(open_action.get("id"))
            payload = solve_answers(observation)
        else:
            action_id = await self._policy.choose(
                observation, legal, msg.get("game_id")
            )
        await self._send(
            {
                "type": models.ACTION,
                "game_id": msg.get("game_id"),
                "action_id": action_id,
                **({"payload": payload} if payload is not None else {}),
            }
        )
        # Surface the choice to the UI as a first-class event.
        await _maybe_await(
            self._on_event(
                {
                    "type": "chose",
                    "game_id": msg.get("game_id"),
                    "seat": msg.get("seat"),
                    "action_id": action_id,
                    "account_id": self.account_id,
                }
            )
        )

    # ---- AgentTown ----------------------------------------------------------

    def ensure_town_policy(self) -> TownPolicy:
        if self._town_policy is None:
            self._town_policy = TownPolicy()
        return self._town_policy

    async def _town_act(self, msg: dict[str, Any]) -> None:
        """Decide and send this tick's town action (runs as a detached task)."""
        try:
            policy = self.ensure_town_policy()
            agent_mode = isinstance(self._policy, AgentPolicy)
            action = await policy.decide(msg, agent_mode)
            await self._send({"type": models.TOWN_ACT, **action})
        except Exception:
            logger.debug("town act failed", exc_info=True)

    async def town_join(self, name: str, avatar: str) -> None:
        await self._send({"type": models.TOWN_JOIN, "name": name, "avatar": avatar})

    async def town_leave(self) -> None:
        await self._send({"type": models.TOWN_LEAVE})

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass


class GameServerClient:
    """Process-global bridge between the game server and local browser panels."""

    def __init__(self) -> None:
        self._primary: _PlayerConn | None = None
        self._sparring: _PlayerConn | None = None
        self._subscribers: set[Any] = set()
        self._self_play = False

    # ---- browser subscription ---------------------------------------------

    def subscribe(self, conn: Any) -> None:
        self._subscribers.add(conn)

    def unsubscribe(self, conn: Any) -> None:
        self._subscribers.discard(conn)

    async def _relay(self, msg: dict[str, Any]) -> None:
        event = str(msg.get("type") or "event")
        payload = {"channel": "games", "event": event, "data": msg}
        for conn in list(self._subscribers):
            try:
                await conn.send_json(payload)
            except Exception:
                logger.debug("failed to relay games event", exc_info=True)

    # ---- connection lifecycle ---------------------------------------------

    @property
    def connected(self) -> bool:
        return self._primary is not None and self._primary.account_id is not None

    async def connect(self, self_play: bool = False) -> dict[str, Any]:
        url, token, policy_name = _settings()
        # `manual` => the primary seat is driven by the agent tool / UI, not auto-play.
        primary_policy = None if policy_name == "manual" else make_policy(policy_name)
        if self._primary is None:
            self._primary = _PlayerConn(url, token, primary_policy, self._relay)
            await self._primary.start()
        self._self_play = self_play
        if self_play and self._sparring is None:
            # Sparring partner always auto-plays (random) so the demo runs itself; it
            # never relays to the browser (the primary already relays public state).
            # It uses its own dev token (not the primary's JWT) so it's a distinct seat.
            self._sparring = _PlayerConn(
                url,
                f"sparring-{_dev_token()}",
                make_policy("random"),
                lambda _msg: None,
            )
            await self._sparring.start()
        status = {
            "type": models.AUTHED,
            "connected": True,
            "account_id": self._primary.account_id,
            "self_play": self_play,
        }
        await self._relay(status)
        return status

    async def disconnect(self) -> None:
        for conn in (self._primary, self._sparring):
            if conn is not None:
                await conn.close()
        self._primary = None
        self._sparring = None
        await self._relay({"type": models.AUTHED, "connected": False})

    # ---- lobby (delegated to the primary seat) ----------------------------

    async def list_tables(self) -> None:
        if self._primary:
            await self._primary.list_tables()

    async def create_table(self, game_id: str) -> None:
        if not self._primary:
            return
        await self._primary.create_table(game_id)
        if self._self_play and self._sparring is not None:
            # Wait for the table to exist, then seat the sparring partner into it.
            table_id = await self._await_open_table(game_id)
            if table_id:
                await self._sparring.join_table(table_id)

    async def join_table(self, table_id: str) -> None:
        if self._primary:
            await self._primary.join_table(table_id)

    async def leave_table(self, table_id: str) -> None:
        if self._primary:
            await self._primary.leave_table(table_id)

    async def run_challenges(self, game_id: str) -> None:
        """Run the challenge track for `game_id` with this node's harness. Results come
        back over the `games` channel as a `challenge_report`."""
        if self._primary:
            await self._primary.run_challenges(game_id)

    # ---- AgentTown (rides the primary connection) ---------------------------

    async def town_join(self, name: str = "", avatar: str = "") -> dict[str, Any]:
        """Spawn (or wake) this account's resident. Auto-connects the node first —
        visiting the town shouldn't require a separate Connect step."""
        if not self.connected:
            await self.connect(False)
        assert self._primary is not None
        await self._primary.town_join(name, avatar)
        return {"ok": True}

    async def town_leave(self) -> dict[str, Any]:
        if self._primary:
            await self._primary.town_leave()
        return {"ok": True}

    def town_whisper(self, text: str) -> dict[str, Any]:
        """Tap the glass: queue a nudge for the resident's next agent tick."""
        if self._primary is None:
            return {"error": "not connected"}
        self._primary.ensure_town_policy().whisper(text)
        return {"ok": True}

    # ---- manual play (agent-tool path) ------------------------------------

    def current_turn(self) -> dict[str, Any] | None:
        """The primary seat's pending turn (observation + legal actions), if any."""
        return self._primary.last_turn if self._primary else None

    async def submit_action(
        self, action_id: str, payload: Any = None
    ) -> dict[str, Any]:
        if not self._primary:
            return {"error": "not connected to a game server"}
        return await self._primary.submit(action_id, payload=payload)

    async def _await_open_table(self, game_id: str, timeout: float = 5.0) -> str | None:
        """Resolve the id of the open table the primary just created by watching the
        relayed `table` events."""
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def watch(msg: dict[str, Any]) -> None:
            if fut.done():
                return
            if msg.get("type") == models.TABLE:
                table = msg.get("table") or {}
                if table.get("game_id") == game_id and table.get("status") == "open":
                    fut.set_result(str(table.get("id")))

        # Temporarily tee the primary's relay through `watch`.
        prev = self._primary._on_event  # type: ignore[union-attr]

        async def tee(msg: dict[str, Any]) -> None:
            await watch(msg)
            await _maybe_await(prev(msg))

        self._primary._on_event = tee  # type: ignore[union-attr]
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            return None
        finally:
            self._primary._on_event = prev  # type: ignore[union-attr]


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value


# Process-global singleton (one node = one game client).
games_client = GameServerClient()
