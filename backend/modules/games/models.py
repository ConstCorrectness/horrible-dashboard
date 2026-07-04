"""Pydantic models for the node games module's HTTP surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GameInfo(BaseModel):
    id: str
    name: str
    min_players: int
    max_players: int


class ToolDefModel(BaseModel):
    """One custom tool in a player's harness: a real function they author."""

    name: str
    description: str = ""
    code: str = ""  # must define `run(args, obs)`
    parameters: dict[str, Any] = {}
    required: list[str] = []


class LoadoutModel(BaseModel):
    """A player's agent harness for a game: strategy context + custom tools."""

    game_id: str
    context: str = ""
    tools: list[ToolDefModel] = []


class TestToolRequest(BaseModel):
    """Dry-run a single tool body against a sample observation, for the editor."""

    code: str
    args: dict[str, Any] = {}
    obs: dict[str, Any] = {}


class TestToolResponse(BaseModel):
    ok: bool
    result: Any = None
    error: str | None = None


class GamesStatus(BaseModel):
    """Whether this node is connected to a game server, and as whom."""

    connected: bool
    account_id: str | None = None
    signed_in: bool = False  # holds a GitHub-issued JWT (vs the dev token)
    display_name: str | None = None
    server_url: str
    policy: str
    games: list[GameInfo] = []


class DevicePollRequest(BaseModel):
    device_code: str
