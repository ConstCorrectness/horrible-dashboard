"""The native client's launch path — what the route hands the binary.

The interesting assertions here are all about **intent**, because for a long time
none of it travelled. The route passed `--connect=127.0.0.1:4000` (nothing has
ever listened there) to a binary that parsed nothing, and once it started passing
a real origin it still passed no *mode*: every launch was "a match on this map, or
open one". That made Train a lie — `match_server.join` with no room id is
join-*or*-create, so pressing Train while anyone was on that map put you in their
firefight — and it left the bot count the menu had just collected with nowhere to
go.

`subprocess.Popen` is stubbed throughout: the debug binary exists in a developer's
checkout, and a test suite that opens a game window is a test suite nobody runs.
"""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app import app


class FakeProc:
    pid = 4242

    def __init__(self, argv, *args, **kwargs) -> None:
        self.argv = argv

    def poll(self) -> None:
        return None

    def wait(self) -> int:
        return 0


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    # Whatever the developer has built, the route resolves to this and nothing on
    # disk decides whether the test runs.
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value",
        lambda key, default=None: (
            str(tmp_path / "hassault-native.exe")
            if key == "hassault.nativeBinaryPath"
            else default
        ),
    )
    (tmp_path / "hassault-native.exe").write_text("")
    return TestClient(app)


def args_for(client: TestClient, **body) -> list[str]:
    res = client.post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", **body}
    )
    assert res.status_code == 200, res.text
    return res.json()["connect_args"]


def test_the_origin_is_the_one_the_caller_reached(client: TestClient) -> None:
    """Read off the request, never assembled from a host and a port.

    The port is not knowable from inside the route — `HORRIBLE_DEV_BACKEND_PORT`
    moves it, Windows' Hyper-V reservations force that regularly, and a packaged
    build binds somewhere else again — but the request arrived at the right
    address by definition.
    """
    args = args_for(client)
    assert any(a.startswith("--server=http://testserver") for a in args), args
    # And exactly one address: the client derives the socket from the origin,
    # because two addresses that must agree is two addresses that can disagree.
    assert not any("--connect=" in a for a in args), args


def test_train_says_so(client: TestClient) -> None:
    assert "--mode=train" in args_for(client, mode="train")


def test_a_bot_count_only_travels_with_host(client: TestClient) -> None:
    """`add_bot` is host-only on the channel, so a count on a join is an
    instruction the server would refuse."""
    hosted = args_for(client, mode="host", bots=3, bot_skill="hard")
    assert "--bots=3" in hosted and "--bot-skill=hard" in hosted

    joined = args_for(client, mode="join", room_id="r1", bots=3)
    assert not any(a.startswith("--bots=") for a in joined), joined


def test_no_bots_is_no_flag(client: TestClient) -> None:
    """Absent, not zero: `--bots=0` is a request the client would act on."""
    assert not any(
        a.startswith("--bots=") for a in args_for(client, mode="host", bots=0)
    )


def test_the_bot_count_is_clamped(client: TestClient) -> None:
    """It arrives from a browser, so it is not trusted."""
    from backend.modules.hassault.match import MAX_PLAYERS

    args = args_for(client, mode="host", bots=9999)
    assert f"--bots={MAX_PLAYERS - 1}" in args, args


def test_a_remote_room_needs_a_room_id(client: TestClient) -> None:
    """The channel refuses a join carrying a host and no room, and that refusal
    would otherwise arrive inside a window that had already opened."""
    res = client.post(
        "/api/hassault/launch_native",
        json={"map_name": "hd_pit", "mode": "join", "host": "abc123"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["launched"] is False
    assert "room id" in (body["message"] or "")


def test_an_older_browser_build_still_validates(client: TestClient) -> None:
    """The request shape grew; a client that has not reloaded still launches.

    It gets the old behaviour by name — `join` — rather than by accident.
    """
    args = args_for(client, room_id="", username="rob", raw_input=True, max_fps=240)
    assert "--mode=join" in args
