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
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.hassault import routes


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


def test_edit_seeds_the_draft_from_the_map(client: TestClient) -> None:
    """The dashboard's Edit button: no `--new`, so the native client opens a
    draft seeded from `map_name` rather than solid rock."""
    args = args_for(client, mode="edit")
    assert "--mode=edit" in args
    assert "--new" not in args


def test_new_starts_the_draft_from_solid_rock(client: TestClient) -> None:
    """The dashboard's New button. Mirrors the native client's own `--new`."""
    args = args_for(client, mode="edit", blank=True)
    assert "--mode=edit" in args
    assert "--new" in args


def test_blank_is_ignored_outside_edit_mode(client: TestClient) -> None:
    """`blank` means nothing to Train, Host, Join or Ranked — only `mode="edit"`
    reads it, so a stray `true` on another launch must not turn into `--new`."""
    args = args_for(client, mode="train", blank=True)
    assert "--new" not in args


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


# --- A build older than its own source -------------------------------------
#
# The trap these close is the one that survived `pick_binary`: that function
# takes the *newest build on disk*, which is still older than the source the
# moment anybody edits the client. The launch then succeeds, the game runs
# perfectly, and simply does not contain the change — a silent failure that
# reads as "my change did not work" rather than "an old binary ran".


@pytest.fixture
def stale_build(tmp_path, monkeypatch):
    """A checkout whose binary predates its source, with cargo stubbed out."""
    binary = tmp_path / "hassault-native.exe"
    binary.write_text("")
    calls: list[tuple[str, str]] = []

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    monkeypatch.setattr(routes, "pick_binary", lambda custom, candidates: str(binary))
    # Newer than the binary by an hour, whatever the developer's tree looks like.
    monkeypatch.setattr(
        routes, "newest_source_mtime", lambda root: binary.stat().st_mtime + 3600
    )
    monkeypatch.setattr(
        routes,
        "build_native_client",
        lambda root, profile: (calls.append((str(root), profile)), (True, ""))[1],
    )
    return calls


def settings_returning(**values):
    return lambda key, default=None: values.get(key, default)


def test_a_binary_older_than_its_source_is_rebuilt_first(
    stale_build, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value", settings_returning()
    )
    res = TestClient(app).post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    body = res.json()
    assert body["launched"] is True
    assert body["rebuilt"] is True
    assert body["stale"] is False
    assert len(stale_build) == 1
    # The profile already on disk — a debug iteration loop is not silently
    # upgraded into a minutes-long optimised build on every launch.
    assert stale_build[0][1] == "release"


def test_a_failed_build_refuses_to_launch_the_old_one(stale_build, monkeypatch) -> None:
    """The whole point: never fall back to the binary that is known to be stale."""
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value", settings_returning()
    )
    monkeypatch.setattr(
        routes, "build_native_client", lambda root, profile: (False, "error[E0277]: no")
    )
    res = TestClient(app).post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    body = res.json()
    assert body["launched"] is False
    assert "E0277" in (body["message"] or "")


def test_auto_build_off_launches_but_says_it_is_stale(stale_build, monkeypatch) -> None:
    """Turning the build off is a choice to run what is on disk — not a licence
    to say nothing about which build that is."""
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value",
        settings_returning(**{"hassault.autoBuildNative": False}),
    )
    res = TestClient(app).post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    body = res.json()
    assert body["launched"] is True
    assert body["stale"] is True
    assert body["rebuilt"] is False
    assert stale_build == []
    assert "predates" in (body["message"] or "")


def test_a_named_binary_is_never_rebuilt(stale_build, tmp_path, monkeypatch) -> None:
    """`hassault.nativeBinaryPath` is somebody naming the build they mean; a
    crate in this checkout says nothing about it."""
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value",
        settings_returning(
            **{"hassault.nativeBinaryPath": str(tmp_path / "hassault-native.exe")}
        ),
    )
    res = TestClient(app).post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    assert res.json()["launched"] is True
    assert stale_build == []


# ---- a launch is a job, not a request ---------------------------------------
#
# The failure these pin: an edited client is compiled before it is started, and a
# cold `cargo build --release` of that crate is minutes. Run inline, the response
# simply did not arrive for all of them — the pane said "Launching…" with nothing
# to read, which is indistinguishable from a hang, and switching tabs unmounted
# the pane and lost the promise it was awaiting while the build carried on
# invisibly. So the work outlives the request that started it, and both the POST
# and the status route report which phase it is in.


@pytest.fixture
def slow_build(stale_build, monkeypatch):
    """A build that does not finish inside the route's inline window."""
    import threading

    release = threading.Event()

    def build(root, profile):
        release.wait(10)
        return True, ""

    monkeypatch.setattr(routes, "build_native_client", build)
    monkeypatch.setattr(routes, "_LAUNCH_INLINE_SECONDS", 0.2)
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value", settings_returning()
    )
    yield release
    release.set()


def test_a_launch_that_is_compiling_says_so_instead_of_hanging(slow_build) -> None:
    client = TestClient(app)
    body = client.post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    ).json()
    assert body["phase"] == "building"
    assert body["launched"] is False
    # And it says which minutes-long thing it is doing. A pending launch whose
    # message is "Launching…" is the state this whole shape exists to end.
    assert "ompiling" in (body["message"] or "")


def test_the_build_survives_the_browser_going_away(slow_build) -> None:
    """The tab-switch half. A pane is unmounted on a tab switch, so the promise
    it was awaiting is dropped — and the launch must still be there to ask
    about when it comes back."""
    # One portal for the whole block, because that is what a server is: without
    # `with`, TestClient spins up and tears down an event loop *per request* and
    # would cancel the very task this is about.
    with TestClient(app) as client:
        client.post(
            "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
        )
        # Nothing kept a reference to that response, exactly as a remounted pane
        # has not: the status route is the whole conversation.
        status = client.get("/api/hassault/launch_native/status").json()
        assert status["phase"] == "building"

        slow_build.set()
        for _ in range(100):
            status = client.get("/api/hassault/launch_native/status").json()
            if status["phase"] in ("launched", "failed"):
                break
            time.sleep(0.1)
    assert status["phase"] == "launched"
    assert status["launched"] is True
    assert status["rebuilt"] is True


def test_a_second_press_joins_the_build_rather_than_starting_another(
    slow_build, stale_build
) -> None:
    """Two `cargo build`s of one crate block on each other's `target/` lock, and
    the second would look exactly like the hang this replaced."""
    client = TestClient(app)
    client.post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    )
    again = client.post(
        "/api/hassault/launch_native", json={"map_name": "hd_pit", "mode": "train"}
    ).json()
    assert again["phase"] == "building"
    assert len(stale_build) <= 1


def test_nothing_launched_is_idle_and_not_a_failure(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_LAUNCH_JOBS", {})
    status = TestClient(app).get("/api/hassault/launch_native/status").json()
    assert status["phase"] == "idle"
    assert status["launched"] is False
    assert status["message"] is None
