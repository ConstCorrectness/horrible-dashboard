"""Tests for the audio module.

The parts worth pinning are the ones whose failures are *silent*: a mixer sized
for the wrong Voicemeeter version writes parameters that go nowhere, a probe that
collapses "could not ask" into "not installed" tells a user to install what they
already have, and a saved routing document read by the wrong schema sends audio
somewhere nobody asked for.

The Remote API itself is not tested against a live Voicemeeter — that would need
one running, and starting it takes over the machine's audio devices. What is
tested is everything around the DLL boundary.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.modules.audio import providers, store, voicemeeter


# ---------------------------------------------------------------------------
# Voicemeeter kinds
# ---------------------------------------------------------------------------


def test_bus_names_differ_per_version() -> None:
    """The counts must come from the running mixer, never be assumed.

    Banana is the version every tutorial covers, so it is the one a hardcoded
    matrix would be sized for. On Potato that silently ignores three strips and
    three buses; sized for Potato instead, `Strip[7].A1` on a Banana is a write
    the API rejects, which reads as "the routing didn't take" with nothing in the
    UI to explain it.
    """
    assert voicemeeter.bus_names(1) == ["A1", "B1"]
    assert voicemeeter.bus_names(2) == ["A1", "A2", "A3", "B1", "B2"]
    assert voicemeeter.bus_names(3) == ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3"]


def test_bus_names_falls_back_to_banana_for_unknown_kind() -> None:
    """An unrecognised kind gets the middle version, not a crash — a future
    Voicemeeter must degrade to a usable mixer, not to none."""
    assert voicemeeter.bus_names(99) == voicemeeter.bus_names(2)


def test_virtual_strips_come_after_physical_ones() -> None:
    """Positional, with nothing in the parameter name to say which is which."""
    assert voicemeeter._strip_is_virtual(0, 2) is False
    assert voicemeeter._strip_is_virtual(2, 2) is False  # last physical on Banana
    assert voicemeeter._strip_is_virtual(3, 2) is True  # first virtual
    assert voicemeeter._strip_is_virtual(4, 3) is False  # Potato has five physical
    assert voicemeeter._strip_is_virtual(5, 3) is True


def test_kind_table_counts_are_self_consistent() -> None:
    """Physical + virtual must equal the total on both axes, or `bus_names`
    produces a list of the wrong length and every cell after it is misaddressed."""
    for kind_id, (
        _,
        strips,
        buses,
        physical_strips,
        physical_buses,
    ) in voicemeeter.KINDS.items():
        assert 0 < physical_strips < strips, kind_id
        assert 0 < physical_buses <= buses, kind_id
        assert len(voicemeeter.bus_names(kind_id)) == buses, kind_id


def test_dll_name_matches_interpreter_bitness(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 32-bit client cannot load the 64-bit DLL, and the OS error it gets looks
    nothing like the real problem. Both ship so the client can choose."""
    import ctypes
    from pathlib import Path

    fake = Path("C:/vm")
    monkeypatch.setattr(voicemeeter, "install_dir", lambda: fake)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    expected = (
        "VoicemeeterRemote64.dll"
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else "VoicemeeterRemote.dll"
    )
    assert voicemeeter.dll_path().name == expected


# ---------------------------------------------------------------------------
# Provider probe — the three states
# ---------------------------------------------------------------------------


def test_linux_missing_pactl_is_uncertain_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinction the whole module is built around.

    No `pactl` on PATH is not evidence that there is no sound server. Reported as
    `installed=False, certain=True` it becomes "you have no audio system", which
    is both wrong and unactionable.
    """
    monkeypatch.setattr(providers, "_run", lambda args: None)
    status = providers.LinuxProvider().status()
    assert status.installed is False
    assert status.certain is False
    assert "could not" in status.note.lower()


def test_linux_reports_pipewire_as_able_to_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux is the platform where we make the cable ourselves — no install."""

    class Result:
        returncode = 0
        stdout = "Server Name: PulseAudio (on PipeWire 1.0.5)\n"
        stderr = ""

    monkeypatch.setattr(providers, "_run", lambda args: Result())
    status = providers.LinuxProvider().status()
    assert status.provider == "pipewire"
    assert status.can_create is True
    # No matrix to drive: routing another app's audio means moving its stream,
    # which is a per-stream operation and not a mixer.
    assert status.can_control is False


def test_linux_refuses_to_destroy_a_device_it_did_not_create() -> None:
    """`pactl unload-module` takes a name and would happily unload the module
    behind the user's real sound card. The prefix check is a safety gate."""
    with pytest.raises(ValueError, match="not created by the dashboard"):
        providers.LinuxProvider().destroy("module-alsa-card")


def test_unknown_platform_is_uncertain() -> None:
    """ "We have never been taught about this OS" is the could-not-ask state."""
    status = providers.UnknownProvider().status()
    assert status.certain is False
    assert status.installed is False


def test_windows_status_is_certain_either_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows can always answer: the registry read and the DLL probe are
    definitive, so `certain` stays True whether or not anything is installed."""
    monkeypatch.setattr(voicemeeter, "is_installed", lambda: False)
    monkeypatch.setattr(providers.WindowsProvider, "_devices", lambda self: [])
    status = providers.WindowsProvider().status()
    assert status.certain is True
    assert status.installed is False
    assert status.can_control is False


def test_windows_installed_but_stopped_is_not_controllable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed and running are different facts. A matrix cannot be read from a
    mixer that is not up, and offering the controls anyway means every cell
    silently fails."""
    monkeypatch.setattr(voicemeeter, "is_installed", lambda: True)
    monkeypatch.setattr(voicemeeter, "is_running", lambda: False)
    monkeypatch.setattr(providers.WindowsProvider, "_devices", lambda self: [])
    status = providers.WindowsProvider().status()
    assert status.installed is True
    assert status.running is False
    assert status.can_control is False
    assert "not running" in status.note


# ---------------------------------------------------------------------------
# Mixer state persistence
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the app database at a temp dir, and clear the schema-init cache —
    which is keyed by path precisely so a fresh tmp dir is not mistaken for an
    already-migrated one."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    yield
    store._initialized.clear()


def test_default_state_changes_nothing_audible() -> None:
    """Installing the mixer must not move anybody's audio: one bus, the system
    default, nothing routed away."""
    state = store.default_state()
    assert len(state["buses"]) == 1
    assert state["buses"][0]["deviceId"] == ""
    assert state["strips"] == []


def test_state_round_trips() -> None:
    state = store.default_state()
    state["buses"].append(
        {
            "id": "B1",
            "label": "Virtual mic",
            "deviceId": "cable-in",
            "deviceLabel": "CABLE Input",
            "gain": -3.0,
            "muted": False,
            "virtual": True,
        }
    )
    state["strips"].append(
        {
            "id": "karaoke",
            "label": "Karaoke",
            "gain": 0,
            "muted": False,
            "sends": {"A1": True, "B1": True},
        }
    )
    store.save_state(state)

    loaded = store.load_state()
    assert len(loaded["buses"]) == 2
    # The whole feature in one assertion: one source, two destinations.
    assert loaded["strips"][0]["sends"] == {"A1": True, "B1": True}


def test_newer_schema_is_discarded_not_partially_read() -> None:
    """A half-understood matrix is not a safe fallback — it routes audio
    somewhere the user did not ask for. Defaults are."""
    with store.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO audio_state (key, version, document) VALUES (?, ?, ?)",
            (
                "default",
                store.SCHEMA_VERSION + 1,
                json.dumps({"buses": [], "strips": []}),
            ),
        )
    assert store.load_state() == store.default_state()


def test_corrupt_document_falls_back_to_defaults() -> None:
    with store.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO audio_state (key, version, document) VALUES (?, ?, ?)",
            ("default", store.SCHEMA_VERSION, "not json"),
        )
    assert store.load_state() == store.default_state()


def test_reset_returns_to_defaults() -> None:
    state = store.default_state()
    state["buses"][0]["label"] = "Studio"
    store.save_state(state)
    assert store.reset_state() == store.default_state()
    assert store.load_state()["buses"][0]["label"] == "Main"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.app import app

    return TestClient(app)


def test_status_route_serves_every_probe_field(client: TestClient) -> None:
    """Tested over HTTP, not against `to_dict()`: a `response_model` filters any
    field it does not declare, silently, so a field added to the probe and
    forgotten in the model reaches the browser as `undefined`."""
    response = client.get("/api/audio/status")
    assert response.status_code == 200
    provider = response.json()["provider"]
    for field in (
        "platform",
        "installed",
        "running",
        "certain",
        "canCreate",
        "canControl",
        "note",
    ):
        assert field in provider, field


def test_mixer_route_round_trips(client: TestClient) -> None:
    state = client.get("/api/audio/mixer").json()
    state["strips"].append(
        {
            "id": "media",
            "label": "Media",
            "gain": 0,
            "muted": False,
            "sends": {"A1": True},
        }
    )
    saved = client.put("/api/audio/mixer", json=state)
    assert saved.status_code == 200
    assert saved.json()["strips"][0]["id"] == "media"
    assert client.get("/api/audio/mixer").json()["strips"][0]["id"] == "media"


def test_creating_a_device_on_windows_explains_rather_than_500s(
    client: TestClient,
) -> None:
    """Only Linux can make a cable. Elsewhere the answer is an install, and a 501
    carrying that sentence is more useful than a stack trace."""
    import platform

    if platform.system() == "Linux":
        pytest.skip("Linux can genuinely create devices")
    response = client.post("/api/audio/devices", json={"label": "Test"})
    assert response.status_code == 501
    assert "install" in response.json()["detail"].lower()
