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


def test_the_newest_build_is_launched_not_the_first_one_found(tmp_path) -> None:
    """The trap this closes: a release binary older than the source.

    The candidates were tried in order, release before debug, so a stale release
    build was silently preferred over a debug one compiled from the current
    checkout — and the symptom is a game missing whatever was just added, with
    nothing saying an old binary was run.
    """
    import os

    from backend.modules.hassault.routes import pick_binary

    old = tmp_path / "release" / "hassault-native.exe"
    new = tmp_path / "debug" / "hassault-native.exe"
    for path in (old, new):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    assert pick_binary("", [str(old), str(new)]) == str(new)
    # Order on the list decides nothing; the timestamp does.
    assert pick_binary("", [str(new), str(old)]) == str(new)


def test_a_named_binary_wins_over_any_build(tmp_path) -> None:
    """Somebody naming a path means that path, however old it is."""
    import os

    from backend.modules.hassault.routes import pick_binary

    named = tmp_path / "mine.exe"
    built = tmp_path / "hassault-native.exe"
    for path in (named, built):
        path.write_text("")
    os.utime(named, (1_000_000, 1_000_000))
    os.utime(built, (2_000_000, 2_000_000))

    assert pick_binary(str(named), [str(built)]) == str(named)
    # A named path that does not exist falls through to the builds rather than
    # failing, so a stale setting is not a client that cannot start.
    assert pick_binary(str(tmp_path / "gone.exe"), [str(built)]) == str(built)


def test_no_build_anywhere_is_none_rather_than_a_guess(tmp_path) -> None:
    from backend.modules.hassault.routes import pick_binary

    assert pick_binary("", [str(tmp_path / "nope.exe"), ""]) is None


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


def test_ranked_travels_as_its_own_mode(client: TestClient) -> None:
    """The native client's only difference for a rated match is this flag: it
    joins through the node exactly as it does for a casual one, and the node opens
    the room somewhere else."""
    args = args_for(client, mode="ranked")
    assert "--mode=ranked" in args


def test_a_ranked_launch_carries_no_bots(client: TestClient) -> None:
    """Bots are host-only, and a rated room has no host to ask — which is the
    point of it. A count sent with a ranked launch would be an instruction the
    server refuses, arriving inside a window that had already opened."""
    args = args_for(client, mode="ranked", bots=4)
    assert not any(a.startswith("--bots=") for a in args)


class DeadProc(FakeProc):
    """A client that is gone before the route answers.

    The real one: the backend is routinely an orphan (`pnpm dev` exits, a
    `--reload` parent dies) holding stdio pipes with no reader left, the client
    inherits them, and its first `eprintln!` panics — exit 101, no window. That
    is now impossible (the child gets a log file, never this process's stdio),
    but every other way a startup can die still exists, so the route observes
    rather than assumes.
    """

    def poll(self) -> int:
        return 101


def test_a_client_that_dies_on_startup_is_not_reported_as_launched(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value",
        lambda key, default=None: (
            str(tmp_path / "hassault-native.exe")
            if key == "hassault.nativeBinaryPath"
            else default
        ),
    )
    (tmp_path / "hassault-native.exe").write_text("")

    res = TestClient(app).post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["launched"] is False
    # The exit code is the whole diagnosis on this path; a message that only said
    # "it did not start" would be the same silence in different words.
    assert "101" in (body["message"] or "")


def test_the_client_never_inherits_this_process_stdio(tmp_path, monkeypatch) -> None:
    """A pipe with no reader kills the client on its first printed line."""
    seen: dict[str, object] = {}

    class Recording(FakeProc):
        def __init__(self, argv, *args, **kwargs) -> None:
            super().__init__(argv)
            seen.update(kwargs)

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", Recording)
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value",
        lambda key, default=None: (
            str(tmp_path / "hassault-native.exe")
            if key == "hassault.nativeBinaryPath"
            else default
        ),
    )
    (tmp_path / "hassault-native.exe").write_text("")

    res = TestClient(app).post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    assert res.status_code == 200, res.text
    assert seen["stdout"] is not None
    assert seen["stdout"] is not subprocess.DEVNULL
    assert seen["stderr"] == subprocess.STDOUT
    assert seen["stdin"] == subprocess.DEVNULL
