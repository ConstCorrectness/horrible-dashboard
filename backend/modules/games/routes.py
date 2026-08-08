"""HTTP surface for the node games module: connection status + the local catalog
of game types the engine can run. Live play happens over the `/ws` `games` channel;
these endpoints are for the lobby panel's initial render.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response

from backend.games_engine.base import list_games
from backend.modules.games import server_auth
from backend.modules.games.agent_sdk import agent_compile_error
from backend.modules.games.client import games_client, resolve_server_url
from backend.modules.games.loadout import (
    HarnessRuntime,
    Loadout,
    ToolDef,
    get_loadout,
    save_loadout,
    tool_name_error,
)
from backend.modules.games.models import (
    ActivateVersionRequest,
    DevicePollRequest,
    DryRunRequest,
    DryRunResponse,
    EnvInfoResponse,
    GameInfo,
    GamesStatus,
    LocalLoginRequest,
    LocalSignupRequest,
    SampleObservationResponse,
    SaveVersionRequest,
    SetCallsignRequest,
    SetKeyRequest,
    LoadoutModel,
    TestToolRequest,
    TestToolResponse,
    ToolDefModel,
    ToolDiagnostic,
    TrainingCapability,
    TrainRunRequest,
    TrainRunResponse,
    ValidateLoadoutResponse,
)
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/games", tags=["games"])


def _catalog() -> list[GameInfo]:
    return [
        GameInfo(
            id=spec.id,
            name=spec.name,
            min_players=spec.min_players,
            max_players=spec.max_players,
            decision_class=spec.decision_class,
            default_policy=spec.default_policy,
            allowed_policies=list(spec.allowed_policies),
            obs_kind=spec.obs_kind,
            pacing=spec.pacing,
        )
        for spec in list_games()
    ]


@router.get("/status", response_model=GamesStatus)
def status() -> GamesStatus:
    account = server_auth.signed_in_account()
    return GamesStatus(
        connected=games_client.connected,
        # The live session's id when there is one, else the id in the stored token.
        # It used to be *only* the former, so a signed-in node that had not yet
        # opened a play socket reported no account id at all — and connection is
        # implicit, so that is the normal state of a freshly-opened client. Anything
        # that identifies "me" in server data (the ladder row on the match setup
        # card, `account.id` in the shared account store) had nothing to match on.
        account_id=(
            games_client._primary.account_id
            if games_client.connected
            else (account["id"] if account else None)
        ),
        # `signed_in` is false once the stored JWT is past its expiry, not merely
        # when the token file is missing — otherwise a month-old session reads as
        # live right up until the play socket rejects it.
        signed_in=account is not None,
        display_name=account["display_name"] if account else None,
        callsign=account.get("handle") if account else None,
        server_url=resolve_server_url(),
        policy=str(get_value("games.policy", "random") or "random"),
        games=_catalog(),
    )


def _to_model(loadout: Loadout) -> LoadoutModel:
    return LoadoutModel(
        game_id=loadout.game_id,
        context=loadout.context,
        tools=[
            ToolDefModel(
                name=t.name,
                description=t.description,
                code=t.code,
                parameters=t.parameters,
                required=t.required,
            )
            for t in loadout.tools
        ],
        model=loadout.model,
        agent_code=loadout.agent_code,
    )


def _from_model(game_id: str, body: LoadoutModel) -> Loadout:
    return Loadout(
        game_id=game_id,
        context=body.context,
        tools=[
            ToolDef(
                name=t.name,
                description=t.description,
                code=t.code,
                parameters=t.parameters,
                required=t.required,
            )
            for t in body.tools
        ],
        model=body.model,
        agent_code=body.agent_code,
    )


# Registered BEFORE the /loadout/{game_id} routes: FastAPI matches in order, and
# the literal "validate" segment must not be swallowed as a game id.
@router.post("/loadout/validate", response_model=ValidateLoadoutResponse)
def validate_loadout_route(body: LoadoutModel) -> ValidateLoadoutResponse:
    """Per-tool diagnostics (name rule + compilation) for the harness editor —
    a broken tool is silently absent in a live match, so surface it here."""
    loadout = _from_model(body.game_id or "_validate", body)
    runtime = HarnessRuntime(loadout)
    taken: set[str] = set()
    diags: list[ToolDiagnostic] = []
    for tool in loadout.tools:
        err = tool_name_error(tool.name, taken) or runtime.compile_error(tool.name)
        taken.add(tool.name)
        diags.append(ToolDiagnostic(name=tool.name, ok=err is None, error=err))
    # A broken `my_agent` entrypoint is silently absent in a match (falls back to random),
    # so surface it here too. Empty agent_code = the default agent → no error.
    agent_error = agent_compile_error(loadout.agent_code)
    return ValidateLoadoutResponse(
        ok=all(d.ok for d in diags) and agent_error is None,
        tools=diags,
        agent_error=agent_error,
    )


@router.get("/loadout/{game_id}", response_model=LoadoutModel)
def get_loadout_route(game_id: str) -> LoadoutModel:
    """The harness for a game (falls back to the `default` loadout)."""
    return _to_model(get_loadout(game_id))


@router.put("/loadout/{game_id}", response_model=LoadoutModel)
def put_loadout_route(game_id: str, body: LoadoutModel) -> LoadoutModel:
    """Overwrite the ACTIVE version of a game's harness in place."""
    return _to_model(save_loadout(_from_model(game_id, body)))


# ---- harness versions (the progression loop) --------------------------------


@router.get("/loadout/{game_id}/versions")
def list_versions_route(game_id: str) -> dict[str, Any]:
    from backend.modules.games import loadout as loadout_mod
    from backend.modules.games import match_log

    return {
        "versions": loadout_mod.list_versions(game_id),
        "stats": match_log.version_stats(game_id),
    }


@router.post("/loadout/{game_id}/versions")
def save_version_route(game_id: str, body: SaveVersionRequest) -> dict[str, Any]:
    """Branch: save as a NEW version (becomes active)."""
    from backend.modules.games import loadout as loadout_mod

    vid = loadout_mod.save_version(
        game_id, _from_model(game_id, body.loadout), body.label
    )
    return {"version_id": vid}


@router.put("/loadout/{game_id}/active")
def activate_version_route(
    game_id: str, body: ActivateVersionRequest
) -> dict[str, Any]:
    from backend.modules.games import loadout as loadout_mod

    ok = loadout_mod.activate_version(game_id, body.version_id)
    return {"ok": ok}


@router.delete("/loadout/{game_id}/versions/{version_id}")
def delete_version_route(game_id: str, version_id: str) -> dict[str, Any]:
    from backend.modules.games import loadout as loadout_mod

    return {"ok": loadout_mod.delete_version(game_id, version_id)}


@router.get("/loadout-templates")
def loadout_templates_route(game_id: str | None = None) -> dict[str, Any]:
    """Starter harnesses for the onboarding wizard's guided first-loadout step."""
    from backend.modules.games.templates import loadout_templates

    templates = loadout_templates()
    if game_id:
        templates = [t for t in templates if t["game_id"] == game_id]
    return {"templates": templates}


@router.get("/agent-starter/{game_id}")
def agent_starter_route(game_id: str) -> dict[str, str]:
    """The starter `my_agent(obs, config)` source to pre-fill the builder's editor for a
    fresh agent on `game_id` (depth varies per game; the editor is the same everywhere)."""
    from backend.modules.games.agent_sdk import starter_agent_source

    return {"game_id": game_id, "agent_code": starter_agent_source(game_id)}


@router.get("/match-log")
def match_log_route(game_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """The node's local match history with loadout/model attribution."""
    from backend.modules.games import match_log

    return {"entries": match_log.list_entries(game_id, limit)}


# ---- model API keys (names only ever leave the node) -------------------------


@router.get("/keys")
def list_keys_route() -> dict[str, Any]:
    from backend.modules.games import model_config

    return {"names": model_config.list_key_names()}


@router.put("/keys/{name}")
def set_key_route(name: str, body: SetKeyRequest) -> dict[str, Any]:
    from backend.modules.games import model_config

    model_config.set_key(name, body.value)
    return {"ok": True}


@router.delete("/keys/{name}")
def delete_key_route(name: str) -> dict[str, Any]:
    from backend.modules.games import model_config

    model_config.delete_key(name)
    return {"ok": True}


@router.post("/test-tool", response_model=TestToolResponse)
async def test_tool_route(body: TestToolRequest) -> TestToolResponse:
    """Compile and run one tool body against a sample observation — the editor's
    'test' button, so a player can iterate on a tool before a live match."""
    runtime = HarnessRuntime(
        Loadout(
            game_id="_test",
            tools=[ToolDef(name="test", description="", code=body.code)],
        )
    )
    if not runtime.has("test"):
        return TestToolResponse(
            ok=False, error=runtime.compile_error("test") or "did not compile"
        )
    result = await runtime.call("test", body.args, body.obs)
    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return TestToolResponse(ok=False, error=str(result["error"]))
    return TestToolResponse(ok=True, result=result)


@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run_route(body: DryRunRequest) -> DryRunResponse:
    """Run the WHOLE harness (context + all tools + the real model) once against
    a sample engine position — the editor's full-loop tester. One-shot: the loop
    is bounded (MAX_HARNESS_ROUNDS), so the finished trace comes back in the
    response."""
    from backend.modules.games import dryrun

    try:
        return await dryrun.run_dry(
            _from_model(body.game_id, body.loadout), body.game_id, body.seed
        )
    except KeyError as exc:
        reason = str(exc.args[0]) if exc.args else f"unknown game {body.game_id!r}"
        return DryRunResponse(ok=False, error=reason)


@router.get("/env/{game_id}", response_model=EnvInfoResponse)
def env_info_route(game_id: str) -> EnvInfoResponse:
    """Does this game have an RL environment, and what does it look like?

    The Train section branches on `has_env`: a `reasoner` game has none, and saying
    so plainly is better than rendering a runner that cannot work."""
    from backend.games_engine.env_adapter import adapter_for

    adapter = adapter_for(game_id)
    if adapter is None:
        return EnvInfoResponse(
            game_id=game_id,
            has_env=False,
            reason=(
                "This is a reasoner game — its actions are payloads (a patch, an "
                "answer), not points in an action space. Use the single-turn dry "
                "run instead."
            ),
        )
    return EnvInfoResponse(
        game_id=game_id,
        has_env=True,
        observation_space=str(adapter.observation_space),
        n_actions=adapter.n_actions,
        training=TrainingCapability(
            self_play=adapter.training.self_play,
            default_episodes=adapter.training.default_episodes,
            max_episodes=adapter.training.max_episodes,
            in_app_optimizer=adapter.training.in_app_optimizer,
            hint=adapter.training.hint,
        ),
    )


@router.post("/train/run", response_model=TrainRunResponse)
async def train_run_route(body: TrainRunRequest) -> TrainRunResponse:
    """Play a script bot for N headless episodes and report how it did.

    Runs on a worker thread: the loop is CPU-bound Python (the engine, the practice
    bot's search, and the player's own policy), so leaving it on the event loop
    would stall every other request — including the websocket that is streaming a
    live match."""
    import asyncio

    from backend.modules.games import trainer

    result = await asyncio.to_thread(
        trainer.run_episodes,
        body.game_id,
        body.code,
        opponent=body.opponent,
        episodes=body.episodes,
        seed=body.seed,
    )
    return TrainRunResponse(**result.to_wire())


@router.get("/sample-observation", response_model=SampleObservationResponse)
def sample_observation_route(game_id: str, seed: int = 0) -> SampleObservationResponse:
    """A realistic opening position for `game_id` — what the Build panel's
    observation inspector shows so a player can see the obs + legal actions they're
    programming against. Cheap (no loadout / model / agent run); resample by seed."""
    from backend.modules.games import dryrun

    try:
        obs, legal = dryrun.sample_observation(game_id, seed)
        return SampleObservationResponse(
            ok=True, game_id=game_id, observation=obs, legal_actions=legal
        )
    except KeyError:
        return SampleObservationResponse(
            ok=False, game_id=game_id, error=f"unknown game {game_id!r}"
        )
    except Exception as exc:  # engine failed to produce a position — surface it
        logger.debug("sample_observation failed for %s", game_id, exc_info=True)
        return SampleObservationResponse(ok=False, game_id=game_id, error=str(exc))


# ---- sign-in (GitHub/Google device flows, proxied to the game server) ------


@router.post("/auth/github/start")
async def github_start_route() -> dict[str, Any]:
    return await server_auth.github_start()


@router.post("/auth/github/poll")
async def github_poll_route(body: DevicePollRequest) -> dict[str, Any]:
    return await server_auth.github_poll(body.device_code)


@router.post("/auth/google/start")
async def google_start_route() -> dict[str, Any]:
    return await server_auth.google_start()


@router.post("/auth/google/poll")
async def google_poll_route(body: DevicePollRequest) -> dict[str, Any]:
    return await server_auth.google_poll(body.device_code)


@router.get("/auth/providers")
async def auth_providers_route() -> dict[str, Any]:
    """Which sign-in flows the connected game server supports
    (`{provider: {device, web}}`, plus `local: {password}`); `{}` when unknown
    (older/unreachable server)."""
    return await server_auth.auth_providers()


# Declared above the `/auth/{provider}/web/*` routes below on purpose: `provider`
# there is a path parameter, FastAPI matches in declaration order, and `local`
# would otherwise be captured by it. Bodies on these two are never recorded —
# see `_REDACT_BODY_PREFIXES` in backend/modules/telemetry/instrument.py.


@router.post("/auth/local/signup")
async def local_signup_route(body: LocalSignupRequest) -> dict[str, Any]:
    """Create an email+password account on the game server and sign this node in."""
    return await server_auth.local_signup(body.email, body.password, body.callsign)


@router.post("/auth/local/login")
async def local_login_route(body: LocalLoginRequest) -> dict[str, Any]:
    """Sign in with an existing email+password."""
    return await server_auth.local_login(body.email, body.password)


@router.post("/auth/callsign")
async def set_callsign_route(body: SetCallsignRequest) -> dict[str, Any]:
    """Claim or rename the callsign — the globally unique handle the ladder and
    HorribleAssault both play you as."""
    return await server_auth.set_callsign(body.callsign)


@router.post("/auth/{provider}/web/start")
async def web_login_start_route(provider: str) -> dict[str, Any]:
    """Begin the redirect (authorization-code) sign-in; returns `{authorize_url}`."""
    return await server_auth.web_login_start(provider)


@router.post("/auth/{provider}/web/poll")
async def web_login_poll_route(provider: str) -> dict[str, Any]:
    """Poll the redirect sign-in until the JWT is captured."""
    return await server_auth.web_login_poll(provider)


@router.post("/signout")
def signout_route() -> dict[str, bool]:
    server_auth.sign_out()
    return {"ok": True}


# ---- profiles ----------------------------------------------------------------
#
# All proxied so the browser talks to one origin and never sees the bearer token.
# Ordering matters here the same way it does for `/auth/local` above: the literal
# `/profile/media` and `/profile/comments/...` segments are declared **before**
# `/profile/{handle}`, or `handle` captures them.


@router.post("/profiles/cards")
async def profile_cards_route(body: dict[str, Any]) -> dict[str, Any]:
    """Avatar, level and status for many callsigns at once — what a friends list
    needs, in one request rather than one per row."""
    handles = body.get("handles") or []
    return await server_auth.profile_cards([str(h) for h in handles])


@router.post("/profile")
async def profile_patch_route(body: dict[str, Any]) -> dict[str, Any]:
    """Update the signed-in account's own profile (artwork, bio, status, showcase)."""
    return await server_auth.profile_patch(body)


@router.post("/profile/media")
async def profile_media_upload_route(
    request: Request, kind: str = "avatar"
) -> dict[str, Any]:
    """Relay a profile image upload. The gates (size, type, quota) are the game
    server's — it owns the volume — so this only carries bytes and identity."""
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    return await server_auth.profile_upload(kind, mime, await request.body())


@router.get("/profile/media/{sha}")
async def profile_media_route(sha: str) -> Response:
    """Serve a profile image through the node, so images come from one origin.

    Content-addressed upstream, so this is safe to cache for a long time: the URL
    can never come to mean different bytes.
    """
    found = await server_auth.profile_media(sha)
    if found is None:
        return Response(status_code=404)
    data, mime = found
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.delete("/profile/comments/{comment_id}")
async def profile_comment_hide_route(comment_id: str) -> dict[str, Any]:
    """Hide a comment. The wall's owner or its author may; the game server checks."""
    return await server_auth.comment_hide(comment_id)


@router.get("/profile/{handle}")
async def profile_route(handle: str) -> dict[str, Any]:
    """Somebody else's profile by callsign — the read that had no endpoint, which
    is why the Plaza card used to show a placeholder bio for every player."""
    return await server_auth.profile_get(handle)


@router.get("/profile/{handle}/comments")
async def profile_comments_route(
    handle: str, before: float | None = None
) -> dict[str, Any]:
    return await server_auth.comments_list(handle, before=before)


@router.post("/profile/{handle}/comments")
async def profile_comment_add_route(
    handle: str, body: dict[str, Any]
) -> dict[str, Any]:
    return await server_auth.comment_add(handle, str(body.get("body") or ""))


@router.get("/replays")
async def replays_route(
    game_id: str | None = None, scope: str = "mine", limit: int = 50
) -> dict[str, Any]:
    """Replay summaries from the game server (`scope=mine` or `scope=public`)."""
    return await server_auth.replays_list(game_id, scope=scope, limit=limit)


@router.get("/replays/{replay_id}")
async def replay_route(replay_id: str) -> dict[str, Any]:
    """One replay with its full event log (both seats' reasoning, post-match)."""
    return await server_auth.replay_get(replay_id)


@router.post("/replays/{replay_id}/publish")
async def replay_publish_route(replay_id: str) -> dict[str, Any]:
    return await server_auth.replay_publish(replay_id)


@router.get("/leaderboard")
async def leaderboard_route(game_id: str = "tictactoe") -> dict[str, Any]:
    return await server_auth.leaderboard(game_id)


@router.get("/challenges/leaderboard")
async def challenge_leaderboard_route(game_id: str = "tictactoe") -> dict[str, Any]:
    return await server_auth.challenge_leaderboard(game_id)
