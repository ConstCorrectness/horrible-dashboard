"""Which modes a room on each map may open in: served to the menus, enforced
by the referee, so a menu never offers a room the server would refuse."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.games_server.app import app
from backend.games_server.hassault_rooms import MENU_MODES, HassaultReferee, SeatConn


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def test_each_map_offers_what_it_is_built_for():
    modes = HassaultReferee.map_modes
    assert modes("hd_aim") == ["dm", "aim", "awp"]
    assert modes("hd_dust2") == ["dm", "tdm", "defuse", "retake"]
    # No modes listed and no sites: deathmatch, either kind.
    assert modes("hd_facility") == ["dm", "tdm"]
    assert "duel" not in MENU_MODES, "a duel is matched by the queue, not opened"


def test_the_maps_endpoint_serves_every_maps_modes():
    body = TestClient(app).get("/hassault/maps").json()
    assert set(body["modes"]) == set(body["maps"])
    assert "hd_aim" in body["maps"]
    assert body["modes"]["hd_aim"] == ["dm", "aim", "awp"]


def test_a_room_in_a_mode_the_map_does_not_offer_is_refused():
    referee = HassaultReferee()

    async def go() -> None:
        with pytest.raises(ValueError, match="does not offer"):
            await referee.join(
                SeatConn(FakeSocket(), "ada", "ada"), "hd_aim", mode="defuse"
            )
        with pytest.raises(ValueError, match="does not offer"):
            await referee.join(
                SeatConn(FakeSocket(), "bob", "bob"), "hd_dust2", mode="aim"
            )
        # And one it does offer opens.
        welcome = await referee.join(
            SeatConn(FakeSocket(), "cy", "cy"), "hd_aim", mode="aim"
        )
        assert welcome["mode"]["id"] == "aim"

    asyncio.run(go())
