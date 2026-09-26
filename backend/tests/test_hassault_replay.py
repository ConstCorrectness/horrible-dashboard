"""Tests for HorribleAssault Match Replay & Spectator Demo Recording Engine (.hrec)."""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import app
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.physics import flat_world
from backend.modules.hassault.replay import (
    ReplayRecorder,
    delete_replay,
    get_replay_data,
    get_replay_metadata,
    init_replay_db,
    list_replays,
)


def test_replay_recorder_lifecycle(tmp_path):
    init_replay_db()
    rec = ReplayRecorder(
        room_id="test_room_1",
        map_name="hd_inferno",
        mode_name="defuse",
        tick_rate=20,
    )
    rec.register_player("p1", "Operative Alpha", 0, is_bot=False)
    rec.register_player("p2", "Operative Bravo", 1, is_bot=True)

    # Record 5 ticks
    for i in range(5):
        rec.record_tick(
            tick=i,
            timestamp=time.time() + i * 0.05,
            players=[
                {
                    "id": "p1",
                    "x": 18.0 + i * 0.1,
                    "y": 28.0,
                    "z": 0.0,
                    "yaw": 1.57,
                    "pitch": 0.0,
                    "health": 100,
                    "armour": 50,
                    "weapon": "assault",
                    "alive": True,
                    "crouch": False,
                    "firing": i == 2,
                },
                {
                    "id": "p2",
                    "x": 24.0,
                    "y": 32.0,
                    "z": 0.0,
                    "yaw": 3.14,
                    "pitch": 0.0,
                    "health": 80,
                    "armour": 0,
                    "weapon": "shotgun",
                    "alive": True,
                    "crouch": False,
                    "firing": False,
                },
            ],
            shared={
                "fx": [{"kind": "shot", "x": 18.0, "y": 28.0, "z": 1.6}]
                if i == 2
                else [],
                "nades": [],
                "zones": [],
                "mode": {"planted": False},
            },
            scores=[1, 0],
        )

    replay_id = rec.finish(winner_team=0)
    assert replay_id == rec.replay_id

    # Verify metadata retrieval
    meta = get_replay_metadata(replay_id)
    assert meta is not None
    assert meta["id"] == replay_id
    assert meta["room_id"] == "test_room_1"
    assert meta["map_name"] == "hd_inferno"
    assert meta["mode"] == "defuse"
    assert meta["ticks"] == 5
    assert meta["winner_team"] == 0
    assert meta["player_count"] == 2
    assert len(meta["players"]) == 2

    # Verify full frame data retrieval
    data = get_replay_data(replay_id)
    assert data is not None
    assert data["version"] == 1
    assert len(data["frames"]) == 5
    assert data["frames"][2]["players"][0]["firing"] is True
    assert len(data["frames"][2]["fx"]) == 1

    # Verify in listing
    all_replays = list_replays(limit=50)
    assert any(r["id"] == replay_id for r in all_replays)

    # Verify deletion
    deleted = delete_replay(replay_id)
    assert deleted is True
    assert get_replay_metadata(replay_id) is None
    assert get_replay_data(replay_id) is None


def test_match_room_recorder_and_attachments():
    world = flat_world(48)
    room = MatchRoom("room_replay_test", "hd_inferno", world, [])
    assert hasattr(room, "recorder")
    assert room.recorder is not None
    assert room.recorder.map_name == "hd_inferno"

    p1 = room.add("TestPlayer", None, team=0)
    assert p1.id in room.recorder.players

    # Send command with gunsmith attachments
    cmd = Command(
        seq=1,
        forward=1.0,
        strafe=0.0,
        jump=False,
        yaw=0.0,
        pitch=0.0,
        dt=0.05,
        attachments={"optic": "red_dot", "barrel": "suppressor"},
    )
    room._handle_combat(p1, cmd, time.time(), time.time() * 1000)
    assert p1.attachments == {"optic": "red_dot", "barrel": "suppressor"}

    snap = p1.snapshot(time.monotonic())
    assert snap.get("attachments") == {"optic": "red_dot", "barrel": "suppressor"}


@pytest.mark.anyio
async def test_replay_rest_api():
    init_replay_db()
    rec = ReplayRecorder("api_room", "hd_dust2", "dm")
    rec.register_player("alpha", "Player Alpha", 0)
    rec.record_tick(1, time.time(), [], {}, [0, 0])
    rep_id = rec.finish(winner_team=1)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List
        resp = await client.get("/api/hassault/replays")
        assert resp.status_code == 200
        items = resp.json()
        assert any(item["id"] == rep_id for item in items)

        # Get metadata
        resp_meta = await client.get(f"/api/hassault/replays/{rep_id}")
        assert resp_meta.status_code == 200
        assert resp_meta.json()["map_name"] == "hd_dust2"

        # Get frames
        resp_frames = await client.get(f"/api/hassault/replays/{rep_id}/frames")
        assert resp_frames.status_code == 200
        assert len(resp_frames.json()["frames"]) == 1

        # Delete
        resp_del = await client.delete(f"/api/hassault/replays/{rep_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "deleted"

        # 404 after deletion
        resp_404 = await client.get(f"/api/hassault/replays/{rep_id}")
        assert resp_404.status_code == 404


def test_replays_live_in_the_data_dir(tmp_path):
    """Through `paths`, not `<repo>/.data`: pinned to the checkout, the file
    landed on a container's ephemeral disk rather than its volume — and every test
    in this file wrote into the developer's real data directory."""
    from backend.modules.hassault import replay

    rec = ReplayRecorder("where", "hd_pit", "dm")
    rec.record_tick(1, time.time(), [], {}, [0, 0])
    rec.finish()
    assert replay.replay_dir() == tmp_path / "hassault" / "replays"
    assert (tmp_path / "hassault" / "replays" / f"{rec.replay_id}.hrec").is_file()


def test_a_long_room_keeps_only_its_most_recent_ticks(monkeypatch):
    """Every tick used to be held in memory until the room closed — ~1 GB an
    hour for a full room nobody closes."""
    from backend.modules.hassault import replay

    monkeypatch.setattr(replay, "MAX_FRAMES", 10)
    rec = ReplayRecorder("long", "hd_pit", "dm")
    for i in range(25):
        rec.record_tick(i, float(i), [], {}, [0, 0])
    rec.finish()
    data = get_replay_data(rec.replay_id)
    assert data is not None
    assert [f["tick"] for f in data["frames"]] == list(range(15, 25))


def test_a_room_can_be_told_not_to_record():
    room = MatchRoom("quiet", "hd_pit", flat_world(48), [], record=False)
    assert room.recorder is None
    # Adding a player is one of the places that reads the recorder.
    room.add("Nobody", None, team=0)
