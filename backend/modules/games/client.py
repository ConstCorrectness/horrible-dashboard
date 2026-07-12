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
        *,
        log_matches: bool = True,
    ) -> None:
        self._url = url
        self._token = token
        # policy None => manual: don't auto-play; the agent tool / UI drives instead.
        self._policy = policy
        self._on_event = on_event
        # The primary seat keeps the local match log (loadout attribution); the
        # sparring seat doesn't — it would double-count self-play games.
        self._log_matches = log_matches
        self._my_seat: int | None = None
        self._match: dict[str, Any] | None = None
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self.account_id: str | None = None
        # Server capabilities advertised on AUTHED — how a node (and the panels it
        # relays to) feature-detects what the connected server supports, so a newer
        # node degrades gracefully against the live deployed server.
        self.caps: list[str] = []
        self.authed = asyncio.Event()
        self.auth_error: str | None = None
        self.last_turn: dict[str, Any] | None = None
        self._play_task: asyncio.Task[None] | None = None
        # Reasoning steps behind the move being decided right now (one pending turn
        # per connection). Relayed live per-step, uploaded to the server per-move.
        self._trace_buffer: list[dict[str, Any]] = []
        # Plaza-arcade held keys for the fighter (set from the browser each frame).
        self._arcade_keys: list[str] = []
        # Compiled `fighter.bot` loadout tool for ranked fighter mode (lazy).
        self._fighter_bot: Any = None
        # The AgentTown resident's mind (created on first use; whispers land here).
        self._town_policy: TownPolicy | None = None

    def set_policy(self, policy: Policy | None) -> None:
        self._policy = policy

    def trace_step(self, step: dict[str, Any]) -> None:
        """Trace sink for this seat's policy: buffer the step for the post-move
        replay upload and relay it live to the browser. Only the primary seat gets
        this sink — an opponent's live reasoning never reaches this browser."""
        turn = self.last_turn
        if turn is None:
            return
        self._trace_buffer.append(step)
        event = {
            "type": "agent_trace",
            "game_id": turn.get("game_id"),
            "table_id": turn.get("table_id"),
            "seat": turn.get("seat"),
            "idx": len(self._trace_buffer) - 1,
            "step": step,
        }
        # Called synchronously from inside the policy loop; relay without blocking it.
        asyncio.get_running_loop().create_task(_maybe_await(self._on_event(event)))

    async def start(self) -> None:
        self._ws = await ws_connect(f"{self._url}/game-ws")
        await self._send({"type": models.AUTH, "token": self._token})
        self._task = asyncio.create_task(self._read_loop())
        await asyncio.wait_for(self.authed.wait(), timeout=10.0)
        if self.auth_error:
            if self._task and not self._task.done():
                self._task.cancel()
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            raise ValueError(f"Game server authentication failed: {self.auth_error}")

    async def _send(self, msg: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg))

    # Public lobby ops (used by the client on the primary seat).
    async def list_tables(self) -> None:
        await self._send({"type": models.LIST_TABLES})

    async def create_table(
        self, game_id: str, ruleset: dict[str, Any] | None = None
    ) -> None:
        msg: dict[str, Any] = {"type": models.CREATE_TABLE, "game_id": game_id}
        if ruleset:
            msg["ruleset"] = ruleset
        await self._send(msg)

    async def join_table(self, table_id: str) -> None:
        await self._send({"type": models.JOIN_TABLE, "table_id": table_id})

    async def leave_table(self, table_id: str) -> None:
        await self._send({"type": models.LEAVE_TABLE, "table_id": table_id})

    async def run_challenges(self, game_id: str) -> None:
        await self._send({"type": models.CHALLENGE_START, "game_id": game_id})

    async def queue_join(
        self, game_id: str, difficulty: str = "standard", placement: bool = False
    ) -> None:
        await self._send(
            {
                "type": models.QUEUE_JOIN,
                "game_id": game_id,
                "difficulty": difficulty,
                "placement": placement,
            }
        )

    async def queue_leave(self) -> None:
        await self._send({"type": models.QUEUE_LEAVE})

    async def challenge_offer(
        self, to_account_id: str, ruleset: dict[str, Any]
    ) -> None:
        await self._send(
            {
                "type": models.CHALLENGE_OFFER,
                "to_account_id": to_account_id,
                "ruleset": ruleset,
            }
        )

    async def challenge_respond(
        self, offer_id: str, response: str, ruleset: dict[str, Any] | None = None
    ) -> None:
        await self._send(
            {
                "type": models.CHALLENGE_RESPOND,
                "offer_id": offer_id,
                "response": response,
                **({"ruleset": ruleset} if ruleset else {}),
            }
        )

    async def rematch_offer(self, table_id: str) -> None:
        await self._send({"type": models.REMATCH_OFFER, "table_id": table_id})

    async def watch_table(self, table_id: str) -> None:
        await self._send({"type": models.WATCH_TABLE, "table_id": table_id})

    async def unwatch_table(self, table_id: str) -> None:
        await self._send({"type": models.UNWATCH_TABLE, "table_id": table_id})

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
        finally:
            if not self.authed.is_set():
                if not self.auth_error:
                    self.auth_error = (
                        "Connection closed before authentication completed"
                    )
                self.authed.set()

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == models.AUTHED:
            self.account_id = str(msg.get("account_id") or "")
            self.caps = [str(c) for c in (msg.get("caps") or [])]
            self.authed.set()
        elif mtype == models.ERROR and not self.authed.is_set():
            self.auth_error = (
                msg.get("message") or msg.get("code") or "Authentication failed"
            )
            self.authed.set()
        elif mtype == models.MATCH_INFO:
            self._match = msg
            self._my_seat = None
            # Declare what plays at this seat (harness version + model) so the
            # replay records it — detached; a failure never blocks the match.
            asyncio.create_task(self._declare_loadout_meta(msg))
        elif mtype == models.GAME_OVER:
            self.last_turn = None  # our turn (if any) is resolved
            self._record_match(msg)
        elif mtype == models.RATING_UPDATE and self._log_matches:
            try:
                from backend.modules.games import match_log

                match_log.attach_rating(
                    str(msg.get("game_id") or ""),
                    float(msg.get("delta") or 0.0),
                    float(msg.get("rating") or 0.0),
                    msg.get("tier"),
                )
            except Exception:
                logger.debug("match log rating attach failed", exc_info=True)
        elif mtype == models.YOUR_TURN:
            self.last_turn = msg
            self._my_seat = int(msg.get("seat") or 0)
            if self._policy is not None:
                # Detached: an agent turn is a model call that can take seconds, and
                # awaiting it here would stall the read loop (and every other event,
                # including the public_state/game_over that would supersede this turn).
                self._play_task = asyncio.create_task(self._play(msg))
        # NOTE: public_state does NOT clear last_turn — in a simultaneous game the
        # opponent's submit broadcasts state while we're still on the clock, and
        # clearing would make the detached _play drop our own move as stale. The
        # turn resolves on game_over, on our own send, or on a re-prompt.
        elif mtype == models.CHALLENGE_SCENARIOS:
            await self._run_challenge_scenarios(msg)
        elif mtype == models.CHALLENGE_UPDATE and msg.get("status") == "accepted":
            # Our offer was accepted: the acceptor is already seated at the fresh
            # table — join it so the match starts without another click.
            table_id = str(msg.get("table_id") or "")
            if table_id:
                await self.join_table(table_id)
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

    def _loadout_attribution(self, game_id: str) -> tuple[str | None, str | None]:
        """(active loadout version, model label) for `game_id` — what we declare to
        the server and stamp into the local match log."""
        try:
            from backend.modules.games import loadout as loadout_mod
            from backend.modules.games import model_config

            version = loadout_mod.active_version_id(game_id)
            config = model_config.parse_model(loadout_mod.get_loadout(game_id).model)
            return version, model_config.model_label(config)
        except Exception:
            logger.debug("loadout attribution failed", exc_info=True)
            return None, None

    async def _declare_loadout_meta(self, match: dict[str, Any]) -> None:
        try:
            game_id = str(match.get("game_id") or "")
            version, label = self._loadout_attribution(game_id)
            if version is None and label is None:
                return
            await self._send(
                {
                    "type": models.LOADOUT_META,
                    "table_id": match.get("table_id"),
                    "version": version,
                    "model_label": label,
                }
            )
        except Exception:
            logger.debug("loadout_meta send failed", exc_info=True)

    def _record_match(self, over: dict[str, Any]) -> None:
        if not self._log_matches:
            return
        try:
            from backend.modules.games import match_log

            game_id = str(over.get("game_id") or "")
            version, label = self._loadout_attribution(game_id)
            match_log.append_entry(
                game_id=game_id,
                table_id=str(over.get("table_id") or ""),
                seat=self._my_seat,
                winner=over.get("winner"),
                loadout_version=version,
                model_label=label,
                replay_id=(self._match or {}).get("replay_id"),
            )
        except Exception:
            logger.debug("match log append failed", exc_info=True)

    # ---- the fighter (fast per-tick paths) ----------------------------------

    # Held-key → action priority (attacks beat movement beat block).
    _ARCADE_MAP: list[tuple[str, str]] = [
        ("k", "special"),
        ("j", "heavy"),
        ("u", "light"),
        ("w", "jump"),
        ("s", "crouch_block"),
        ("a", "left"),
        ("d", "right"),
    ]

    def set_arcade_keys(self, keys: list[str]) -> None:
        self._arcade_keys = [str(k).lower() for k in keys]

    def _arcade_action(self, legal_ids: set[str]) -> str:
        for key, action in self._ARCADE_MAP:
            if key in self._arcade_keys and action in legal_ids:
                return action
        return "idle"

    async def _play_fighter(
        self,
        msg: dict[str, Any],
        observation: dict[str, Any],
        legal: list[dict[str, Any]],
    ) -> None:
        legal_ids = {str(a.get("id")) for a in legal}
        if self._arcade_keys or self._policy is None:
            # Arcade seat (human at the keyboard) — map held keys, default idle.
            action_id = self._arcade_action(legal_ids)
        else:
            action_id = self._fighter_bot_action(observation, legal_ids)
        if self.last_turn is not msg:
            return
        await self._send(
            {
                "type": models.ACTION,
                "game_id": "fighter",
                "action_id": action_id if action_id in legal_ids else "idle",
            }
        )
        if self.last_turn is msg:
            self.last_turn = None

    def _fighter_bot_action(
        self, observation: dict[str, Any], legal_ids: set[str]
    ) -> str:
        """Ranked fighter: run the compiled `fighter.bot` loadout tool (a pure
        function, no model) to pick this tick's action. Any failure → idle."""
        try:
            if self._fighter_bot is None:
                from backend.modules.games.loadout import HarnessRuntime, get_loadout

                runtime = HarnessRuntime(get_loadout("fighter"))
                self._fighter_bot = runtime if runtime.has("fighter.bot") else False
            if not self._fighter_bot:
                return "idle"
            # HarnessRuntime.call is async only to await async tools; the fighter
            # bot is sync, so call the compiled fn directly for speed.
            fn = self._fighter_bot._compiled.get("fighter.bot")
            result = fn({}, observation) if fn else None
            action = result if isinstance(result, str) else (result or {}).get("action")
            return str(action) if str(action) in legal_ids else "idle"
        except Exception:
            logger.debug("fighter.bot failed; idling", exc_info=True)
            return "idle"

    async def _solve_task(
        self, msg: dict[str, Any], observation: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Drive a code-task open action (bug hunt) with the TaskAgent, using the
        loadout's model. Returns None to fall back to the baseline (no model, or
        the agent errored) so a table never hangs. Traces flow to the same sink."""
        from backend.modules.agent.routes import _load_config
        from backend.modules.games import model_client, model_config
        from backend.modules.games.loadout import get_loadout
        from backend.modules.games.task_agent import TaskAgent

        game_id = str(msg.get("game_id") or "")
        import httpx

        loadout_model = model_config.parse_model(get_loadout(game_id).model)
        try:
            if loadout_model is not None:
                headers = model_client.headers_for(loadout_model)

                async with httpx.AsyncClient(timeout=120.0, headers=headers) as client:

                    async def chat(
                        messages: list[dict[str, Any]], tools: list[dict[str, Any]]
                    ) -> Any:
                        return await model_client.chat(
                            client, loadout_model, messages, tools
                        )

                    agent = TaskAgent(
                        chat_fn=chat, game_id=game_id, trace=self.trace_step
                    )
                    return await agent.run(observation)

            config = _load_config()
            if config is None:
                return None  # no model available — baseline handles it
            from backend.modules.agent import providers as P

            info = P.provider_for(config.provider)
            endpoint = config.endpoint or info.default_endpoint
            async with httpx.AsyncClient(timeout=120.0) as client:

                async def chat2(
                    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
                ) -> Any:
                    return await P.chat(
                        client, info, endpoint, config.model, messages, tools
                    )

                agent = TaskAgent(chat_fn=chat2, game_id=game_id, trace=self.trace_step)
                return await agent.run(observation)
        except Exception:
            logger.debug("task agent failed; falling back to baseline", exc_info=True)
            return None

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

        # The fighter ticks ~once a second and must answer *fast* — no model call
        # per frame. Arcade (human) play maps held keys; ranked play runs the
        # compiled `fighter.bot` loadout tool directly. Either way, answer inline.
        if msg.get("game_id") == "fighter":
            await self._play_fighter(msg, observation, legal)
            return

        self._trace_buffer = []

        # Open actions (duels, code games): the turn wants *content*, not a pick —
        # run the built-in baseline solver so auto-play always finishes. Harness
        # skill drives through the manual path (`game.chooseAction` + payload).
        from backend.games_engine.baseline import find_open_action, solve_open_action

        open_action = find_open_action(legal)
        payload: Any = None
        if open_action is not None:
            action_id = str(open_action.get("id"))
            kind = str((open_action.get("params") or {}).get("payload") or "")
            # A "files" open action (bug hunt) is a long-horizon coding session, not
            # a single move — drive it with the TaskAgent when we have a real model.
            if kind == "files":
                payload = await self._solve_task(msg, observation)
            if payload is None:
                payload = solve_open_action(open_action, observation)
        else:
            action_id = await self._policy.choose(
                observation, legal, msg.get("game_id")
            )
        # Since we run detached, the turn may have been superseded while the policy
        # thought (server timeout auto-played, game ended): drop a stale answer.
        if self.last_turn is not msg:
            return
        # Upload the reasoning *before* the move: the action may end the game, and
        # the replay is frozen the moment the referee finishes.
        if self._trace_buffer:
            await self._send(
                {
                    "type": models.MOVE_TRACE,
                    "game_id": msg.get("game_id"),
                    "table_id": msg.get("table_id"),
                    "seat": msg.get("seat"),
                    "action_id": action_id,
                    "steps": self._trace_buffer[:50],
                }
            )
        await self._send(
            {
                "type": models.ACTION,
                "game_id": msg.get("game_id"),
                "action_id": action_id,
                **({"payload": payload} if payload is not None else {}),
            }
        )
        if self.last_turn is msg:
            self.last_turn = None  # resolved by our own send
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

    # ---- The Plaza (human social layer) -------------------------------------

    async def social_join(self, name: str, avatar: str) -> None:
        await self._send({"type": models.SOCIAL_JOIN, "name": name, "avatar": avatar})

    async def social_leave(self) -> None:
        await self._send({"type": models.SOCIAL_LEAVE})

    async def social_move(self, x: float, y: float) -> None:
        await self._send({"type": models.SOCIAL_MOVE, "x": x, "y": y})

    async def social_room(self, room: str) -> None:
        await self._send({"type": models.SOCIAL_ROOM, "room": room})

    async def social_say(self, text: str, emote: bool = False) -> None:
        await self._send(
            {"type": models.SOCIAL_EMOTE if emote else models.SOCIAL_SAY, "text": text}
        )

    async def social_invite(self, account_id: str, game_id: str) -> None:
        await self._send(
            {"type": models.SOCIAL_INVITE, "account_id": account_id, "game_id": game_id}
        )

    async def friend_action(self, kind: str, account_id: str) -> None:
        """`kind` is one of request/accept/remove — the matching FRIEND_* message."""
        mtype = {
            "request": models.FRIEND_REQUEST,
            "accept": models.FRIEND_ACCEPT,
            "remove": models.FRIEND_REMOVE,
        }[kind]
        await self._send({"type": mtype, "account_id": account_id})

    async def friend_list(self) -> None:
        await self._send({"type": models.FRIEND_LIST})

    async def profile_get(self) -> None:
        await self._send({"type": models.PROFILE_GET})

    async def profile_set(
        self, avatar: str | None, bio: str | None, handle: str | None = None
    ) -> None:
        msg: dict[str, Any] = {"type": models.PROFILE_SET}
        if avatar is not None:
            msg["avatar"] = avatar
        if bio is not None:
            msg["bio"] = bio
        if handle is not None:
            msg["handle"] = handle
        await self._send(msg)

    async def close(self) -> None:
        if self._play_task is not None and not self._play_task.done():
            self._play_task.cancel()
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
        if self._primary is None:
            # `manual` => the primary seat is driven by the agent tool / UI, not
            # auto-play. The policy's trace sink is the conn itself (live reasoning
            # relay + replay upload), so the conn is built first.
            self._primary = _PlayerConn(url, token, None, self._relay)
            if policy_name != "manual":
                self._primary.set_policy(
                    make_policy(policy_name, trace=self._primary.trace_step)
                )
            try:
                await self._primary.start()
            except Exception as e:
                self._primary = None
                from backend.modules.games.server_auth import get_token, sign_out

                if get_token() == token:
                    logger.warning(
                        "Stored game server token was rejected. Signing out."
                    )
                    sign_out()
                raise e
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
            "caps": self._primary.caps,
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

    async def create_table(
        self, game_id: str, ruleset: dict[str, Any] | None = None
    ) -> None:
        if not self._primary:
            return
        await self._primary.create_table(game_id, ruleset)
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

    # ---- ranked queue + negotiation (delegated to the primary seat) ---------

    async def queue_join(
        self, game_id: str, difficulty: str = "standard", placement: bool = False
    ) -> None:
        await (await self._ensure_primary()).queue_join(game_id, difficulty, placement)

    async def queue_leave(self) -> None:
        if self._primary:
            await self._primary.queue_leave()

    async def challenge_offer(
        self, to_account_id: str, ruleset: dict[str, Any]
    ) -> None:
        await (await self._ensure_primary()).challenge_offer(to_account_id, ruleset)

    async def challenge_respond(
        self, offer_id: str, response: str, ruleset: dict[str, Any] | None = None
    ) -> None:
        if self._primary:
            await self._primary.challenge_respond(offer_id, response, ruleset)

    async def rematch_offer(self, table_id: str) -> None:
        if self._primary:
            await self._primary.rematch_offer(table_id)

    async def watch_table(self, table_id: str) -> None:
        await (await self._ensure_primary()).watch_table(table_id)

    async def unwatch_table(self, table_id: str) -> None:
        if self._primary:
            await self._primary.unwatch_table(table_id)

    def set_arcade_keys(self, keys: list[str]) -> None:
        """Held keys for the fighter arcade — set on the primary seat (the human's
        seat). Answered instantly on each of that seat's ticks."""
        if self._primary:
            self._primary.set_arcade_keys(keys)

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

    # ---- The Plaza (human social layer, rides the primary connection) -------

    async def _ensure_primary(self) -> _PlayerConn:
        """The Plaza should be reachable without a separate Connect step, so joining
        auto-connects the node first (like the town)."""
        if not self.connected:
            await self.connect(False)
        assert self._primary is not None
        return self._primary

    async def social_join(self, name: str = "", avatar: str = "") -> dict[str, Any]:
        await (await self._ensure_primary()).social_join(name, avatar)
        return {"ok": True}

    async def social_leave(self) -> dict[str, Any]:
        if self._primary:
            await self._primary.social_leave()
        return {"ok": True}

    async def social_move(self, x: float, y: float) -> dict[str, Any]:
        if self._primary:
            await self._primary.social_move(x, y)
        return {"ok": True}

    async def social_room(self, room: str) -> dict[str, Any]:
        if self._primary:
            await self._primary.social_room(room)
        return {"ok": True}

    async def social_say(self, text: str, emote: bool = False) -> dict[str, Any]:
        if self._primary:
            await self._primary.social_say(text, emote)
        return {"ok": True}

    async def social_invite(self, account_id: str, game_id: str) -> dict[str, Any]:
        await (await self._ensure_primary()).social_invite(account_id, game_id)
        return {"ok": True}

    async def friend_action(self, kind: str, account_id: str) -> dict[str, Any]:
        await (await self._ensure_primary()).friend_action(kind, account_id)
        return {"ok": True}

    async def friend_list(self) -> dict[str, Any]:
        if self._primary:
            await self._primary.friend_list()
        return {"ok": True}

    async def profile_get(self) -> dict[str, Any]:
        await (await self._ensure_primary()).profile_get()
        return {"ok": True}

    async def profile_set(
        self,
        avatar: str | None = None,
        bio: str | None = None,
        handle: str | None = None,
    ) -> dict[str, Any]:
        await (await self._ensure_primary()).profile_set(avatar, bio, handle)
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
