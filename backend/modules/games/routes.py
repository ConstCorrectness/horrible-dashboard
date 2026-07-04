"""HTTP surface for the node games module: connection status + the local catalog
of game types the engine can run. Live play happens over the `/ws` `games` channel;
these endpoints are for the lobby panel's initial render.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.games_engine.base import list_games
from backend.modules.games import server_auth
from backend.modules.games.client import DEFAULT_SERVER_URL, games_client
from backend.modules.games.loadout import (
    HarnessRuntime,
    Loadout,
    ToolDef,
    get_loadout,
    save_loadout,
)
from backend.modules.games.models import (
    DevicePollRequest,
    GameInfo,
    GamesStatus,
    LoadoutModel,
    TestToolRequest,
    TestToolResponse,
    ToolDefModel,
)
from backend.modules.settings.routes import get_value

router = APIRouter(prefix="/games", tags=["games"])


def _catalog() -> list[GameInfo]:
    return [
        GameInfo(
            id=spec.id,
            name=spec.name,
            min_players=spec.min_players,
            max_players=spec.max_players,
        )
        for spec in list_games()
    ]


@router.get("/status", response_model=GamesStatus)
def status() -> GamesStatus:
    name = server_auth.signed_in_name()
    return GamesStatus(
        connected=games_client.connected,
        account_id=(
            games_client._primary.account_id if games_client.connected else None
        ),
        signed_in=name is not None,
        display_name=name,
        server_url=str(
            get_value("games.serverUrl", DEFAULT_SERVER_URL) or DEFAULT_SERVER_URL
        ),
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
    )


@router.get("/loadout/{game_id}", response_model=LoadoutModel)
def get_loadout_route(game_id: str) -> LoadoutModel:
    """The harness for a game (falls back to the `default` loadout)."""
    return _to_model(get_loadout(game_id))


@router.put("/loadout/{game_id}", response_model=LoadoutModel)
def put_loadout_route(game_id: str, body: LoadoutModel) -> LoadoutModel:
    loadout = Loadout(
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
    )
    return _to_model(save_loadout(loadout))


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


# ---- sign-in (GitHub device flow, proxied to the game server) --------------


@router.post("/auth/github/start")
async def github_start_route() -> dict[str, Any]:
    return await server_auth.github_start()


@router.post("/auth/github/poll")
async def github_poll_route(body: DevicePollRequest) -> dict[str, Any]:
    return await server_auth.github_poll(body.device_code)


@router.post("/signout")
def signout_route() -> dict[str, bool]:
    server_auth.sign_out()
    return {"ok": True}


@router.get("/leaderboard")
async def leaderboard_route(game_id: str = "tictactoe") -> dict[str, Any]:
    return await server_auth.leaderboard(game_id)


@router.get("/challenges/leaderboard")
async def challenge_leaderboard_route(game_id: str = "tictactoe") -> dict[str, Any]:
    return await server_auth.challenge_leaderboard(game_id)
