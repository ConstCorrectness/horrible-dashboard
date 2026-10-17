"""Tests for the music module: the library (import, dedup, search, serving), the
resolver, playlist import jobs and re-sync, the session's transport semantics,
scrobbling, enrichment rules, and the agent tools.

Nothing touches the network. Sources are replaced by `FakeSource`, which "downloads"
by writing a generated WAV; listeners are fakes; enrichment hooks are cleared.
"""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.music import (
    acquire,
    agent_tools,
    curator,
    enrich,
    jobs,
    library,
    lyrics,
    resolve,
)
from backend.modules.music import listeners as music_listeners
from backend.modules.music.importers.files import AppleMusicExport, CSVImport, M3UImport
from backend.modules.music.providers import (
    DescribedTrack,
    FetchedFile,
    ImportedPlaylist,
    PlaylistSummary,
    ProviderError,
    TrackCandidate,
    importers,
    listeners,
    sources,
)
from backend.modules.music.session import MusicSession


def _wav(path: Path, seconds: float = 1.0, tone: int = 0) -> Path:
    """A tiny silent-ish WAV. `tone` changes the bytes so hashes differ."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(8000 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(bytes([(128 + tone + i) % 256 for i in range(frames)]))
    return path


class FakeSource:
    id = "fake"
    label = "Fake"

    def __init__(self) -> None:
        self.catalog: dict[str, list[TrackCandidate]] = {}
        self.fetched: list[str] = []

    def configured(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 10) -> list[TrackCandidate]:
        for key, cands in self.catalog.items():
            if key.lower() in query.lower():
                return cands[:limit]
        return []

    async def fetch(
        self, candidate: TrackCandidate, target: Path, file_id: str
    ) -> FetchedFile:
        self.fetched.append(candidate.ref)
        path = _wav(target / f"{file_id}.wav", tone=len(self.fetched))
        return FetchedFile(
            path=path,
            title=candidate.title,
            artist=candidate.artist,
            duration=candidate.duration,
        )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No /ws fan-out, a fresh session, only fake providers, no enrichment hooks."""

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("backend.modules.music.events.broadcast_event", _noop)
    fresh = MusicSession()
    for target in (
        "backend.modules.music.session.session",
        "backend.modules.music.routes.session",
        "backend.modules.music.agent_tools.session",
    ):
        monkeypatch.setattr(target, fresh)
    monkeypatch.setattr(acquire, "_ready_hooks", [])
    fake = FakeSource()
    monkeypatch.setattr(sources, "_items", {"fake": fake})
    monkeypatch.setattr(listeners, "_items", {})
    monkeypatch.setattr(resolve, "min_confidence", lambda: 0.6)
    return {"session": fresh, "source": fake}


@pytest.fixture
def session(isolated):
    return isolated["session"]


@pytest.fixture
def source(isolated):
    return isolated["source"]


def _track(title="Song", artist="Artist", status="ready", **fields):
    return library.create_track(title=title, artist=artist, status=status, **fields)


def _ready_track_with_file(title="Song", artist="Artist", seconds=1.0, tone=0):
    row = _track(title=title, artist=artist, duration=200.0)
    path = _wav(library.files_dir() / f"{row['id']}.wav", seconds=seconds, tone=tone)
    return library.update_track(row["id"], filename=path.name)


async def _drain():
    """Let detached download tasks finish."""
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.sleep(0.01)


# --- library --------------------------------------------------------------------


def test_import_folder_splits_filenames_and_dedups(data_dir, tmp_path):
    folder = tmp_path / "in"
    _wav(folder / "Toto - Africa.wav", tone=1)
    _wav(folder / "sub" / "Rosanna.wav", tone=2)
    (folder / "notes.txt").write_text("not music")

    result = asyncio.run(acquire.import_files([str(folder)]))
    titles = sorted((t["artist"], t["title"]) for t in result["imported"])
    assert titles == [("", "Rosanna"), ("Toto", "Africa")]
    assert all(t["status"] == "ready" and t["filename"] for t in result["imported"])
    # The library owns copies; the originals are untouched.
    assert (folder / "Toto - Africa.wav").exists()

    again = asyncio.run(acquire.import_files([str(folder)]))
    assert again["imported"] == [] and len(again["skipped"]) == 2


def test_import_reads_a_sidecar_lrc(data_dir, tmp_path):
    song = _wav(tmp_path / "in" / "Toto - Africa.wav")
    song.with_suffix(".lrc").write_text(
        "[00:01.50]I hear the drums\n[00:04.00]echoing tonight\n"
    )
    row = asyncio.run(acquire.import_files([str(song)]))["imported"][0]
    found = lyrics.cached(row["id"])
    assert found["source"] == "sidecar"
    assert found["synced"] == [[1.5, "I hear the drums"], [4.0, "echoing tonight"]]


def test_fts_search_prefix_matches_across_fields(data_dir):
    _track("Africa", "Toto", album="Toto IV")
    _track("Dreams", "Fleetwood Mac", album="Rumours")
    assert [t["title"] for t in library.list_tracks("toto afr")] == ["Africa"]
    assert [t["title"] for t in library.list_tracks("rumou")] == ["Dreams"]
    # An edit re-indexes (the update trigger).
    track = library.list_tracks("dreams")[0]
    library.edit_track(track["id"], title="Go Your Own Way")
    assert library.list_tracks("dreams") == []
    assert library.get_track(track["id"])["locked_fields"] == ["title"]


def _client() -> TestClient:
    from backend.modules.music.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_media_serves_ranges_and_refuses_escapes(data_dir):
    track = _ready_track_with_file()
    client = _client()
    full = client.get(f"/api/music/media/{track['id']}")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
    part = client.get(f"/api/music/media/{track['id']}", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.content == full.content[:10]
    tail = client.get(f"/api/music/media/{track['id']}", headers={"Range": "bytes=-4"})
    assert tail.content == full.content[-4:]

    escaped = _track(title="sneaky")
    library.update_track(escaped["id"], filename="../../app.db")
    assert client.get(f"/api/music/media/{escaped['id']}").status_code == 404


def test_track_listing_carries_new_fields_over_http(data_dir):
    """`response_model` silently drops fields the model doesn't declare."""
    _track("Africa", "Toto", external_ids={"spotify": "abc"}, match_confidence=0.9)
    body = _client().get("/api/music/tracks").json()
    row = body["tracks"][0]
    assert row["external_ids"] == {"spotify": "abc"}
    assert row["match_confidence"] == 0.9
    assert body["total"] == 1


# --- download ---------------------------------------------------------------------


def test_download_queues_then_flips_ready(data_dir, session, source):
    cand = TrackCandidate(
        source="fake", ref="vid1", title="Africa", artist="Toto", duration=1.0
    )

    async def run():
        row = await acquire.start_download(cand)
        assert row["status"] == "queued"
        await session.add([row], mode="play")
        assert session.snapshot().now_playing.ready is False
        await _drain()
        return row

    row = asyncio.run(run())
    done = library.get_track(row["id"])
    assert done["status"] == "ready" and done["content_hash"]
    assert session.snapshot().now_playing.ready is True
    # Asking again returns the same row instead of a second download.
    again = asyncio.run(acquire.start_download(cand))
    assert again["id"] == row["id"] and source.fetched == ["vid1"]


# --- resolver --------------------------------------------------------------------


def test_resolver_penalises_versions_and_wrong_lengths():
    desc = DescribedTrack(title="Africa", artist="Toto", duration=295)
    studio = TrackCandidate(
        source="fake",
        ref="a",
        title="Africa",
        artist="Toto",
        duration=296,
        channel="Toto - Topic",
    )
    live = TrackCandidate(
        source="fake",
        ref="b",
        title="Africa (Live in Paris)",
        artist="Toto",
        duration=297,
    )
    long = TrackCandidate(
        source="fake", ref="c", title="Africa", artist="Toto", duration=600
    )
    karaoke = TrackCandidate(
        source="fake",
        ref="d",
        title="Africa (Karaoke Version)",
        artist="Toto",
        duration=295,
    )
    s = {c.ref: resolve.score(desc, c).score for c in (studio, live, long, karaoke)}
    assert s["a"] > 0.9
    assert s["a"] > s["b"] and s["a"] > s["c"] and s["d"] < 0.4
    # Asking for the live version un-penalises it.
    asked = DescribedTrack(title="Africa (Live in Paris)", artist="Toto", duration=297)
    assert resolve.score(asked, live).score > 0.8


# --- importers -----------------------------------------------------------------------


def test_apple_txt_export_is_utf16_tab_separated():
    text = "Name\tArtist\tComposer\tAlbum\tTime\r\nAfrica\tToto\t\tToto IV\t295\r\nDreams\tFleetwood Mac\t\tRumours\t257\r\n"
    playlist = AppleMusicExport().parse(
        "Road Trip.txt", b"\xff\xfe" + text.encode("utf-16-le")
    )
    assert playlist.name == "Road Trip" and playlist.origin == "apple-music:road trip"
    assert [(t.artist, t.title, t.album, t.duration) for t in playlist.tracks] == [
        ("Toto", "Africa", "Toto IV", 295.0),
        ("Fleetwood Mac", "Dreams", "Rumours", 257.0),
    ]


def test_apple_xml_export_reads_the_playlist_order():
    import plistlib

    data = plistlib.dumps(
        {
            "Tracks": {
                "10": {
                    "Name": "Africa",
                    "Artist": "Toto",
                    "Total Time": 295000,
                    "Persistent ID": "AAA",
                },
                "11": {
                    "Name": "Dreams",
                    "Artist": "Fleetwood Mac",
                    "Total Time": 257000,
                },
            },
            "Playlists": [
                {
                    "Name": "Faves",
                    "Playlist Items": [{"Track ID": 11}, {"Track ID": 10}],
                }
            ],
        }
    )
    playlist = AppleMusicExport().parse("x.xml", data)
    assert playlist.name == "Faves"
    assert [t.title for t in playlist.tracks] == ["Dreams", "Africa"]
    assert (
        playlist.tracks[1].external_ids == {"apple": "AAA"}
        and playlist.tracks[1].duration == 295.0
    )


def test_m3u_and_csv_parse():
    m3u = b"#EXTM3U\n#EXTINF:295,Toto - Africa\nmusic/Toto - Africa.mp3\nhttps://example.com/stream\n"
    pl = M3UImport().parse("mix.m3u", m3u)
    assert [(t.artist, t.title, t.duration) for t in pl.tracks] == [
        ("Toto", "Africa", 295.0),
        ("", "stream", None),
    ]
    assert pl.tracks[0].external_ids == {"path": "music/Toto - Africa.mp3"}

    csv_data = (
        "Track URI,Track Name,Artist Name(s),Album Name,Duration (ms),ISRC\n"
        'spotify:track:123,Africa,"Toto, Someone",Toto IV,295000,USSM18200001\n'
    ).encode()
    pl = CSVImport().parse("exportify.csv", csv_data)
    track = pl.tracks[0]
    assert (track.title, track.artist, track.duration) == ("Africa", "Toto", 295.0)
    assert track.external_ids == {"isrc": "USSM18200001", "spotify": "123"}


class FakeAccountImporter:
    id = "fakeacct"
    label = "Fake account"
    kind = "account"

    def __init__(self) -> None:
        self.tracks: list[DescribedTrack] = []

    def configured(self) -> bool:
        return True

    async def list_playlists(self) -> list[PlaylistSummary]:
        return [PlaylistSummary(ref="p1", name="Mix")]

    async def read_playlist(self, ref: str) -> ImportedPlaylist:
        return ImportedPlaylist(
            name="Mix", origin=f"fakeacct:{ref}", tracks=list(self.tracks)
        )

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        raise ProviderError("account only")


def _cand(ref, title, artist, duration):
    return TrackCandidate(
        source="fake",
        ref=ref,
        title=title,
        artist=artist,
        duration=duration,
        channel=f"{artist} - Topic",
    )


def test_import_job_downloads_reviews_and_resyncs(data_dir, source, monkeypatch):
    importer = FakeAccountImporter()
    monkeypatch.setattr(importers, "_items", {"fakeacct": importer})
    source.catalog = {
        "Africa": [_cand("af", "Africa", "Toto", 295)],
        "Dreams": [_cand("dr", "Dreams", "Fleetwood Mac", 257)],
        # Only a wrong-length cover exists: must go to review, not download.
        "Obscure": [
            TrackCandidate(
                source="fake",
                ref="ob",
                title="Obscure (cover)",
                artist="Nobody",
                duration=900,
            )
        ],
        "Rosanna": [_cand("ro", "Rosanna", "Toto", 331)],
    }
    importer.tracks = [
        DescribedTrack(
            "Africa", "Toto", duration=295, external_ids={"spotify": "s-af"}
        ),
        DescribedTrack(
            "Dreams", "Fleetwood Mac", duration=257, external_ids={"spotify": "s-dr"}
        ),
        DescribedTrack(
            "Obscure", "Somebody", duration=200, external_ids={"spotify": "s-ob"}
        ),
    ]

    async def run(ref):
        job = await jobs.import_from_account("fakeacct", ref)
        await _drain()
        return jobs.get_job(job["id"], with_items=True)

    job = asyncio.run(run("p1"))
    assert job["status"] == "done"
    assert [i["status"] for i in job["items"]] == ["done", "done", "review"]
    assert sorted(source.fetched) == ["af", "dr"]
    playlist = library.get_playlist(job["playlist_id"], with_tracks=True)
    assert [t["title"] for t in playlist["tracks"]] == ["Africa", "Dreams", "Obscure"]
    review = library.list_tracks(status="needs_review")
    assert len(review) == 1 and review[0]["candidates"][0]["ref"] == "ob"
    # Importer metadata wins over the candidate's.
    africa = playlist["tracks"][0]
    assert africa["external_ids"]["spotify"] == "s-af" and africa["status"] == "ready"

    # Re-sync: one track removed, one added. Only the new one downloads; the
    # playlist mirrors the source's new order; it is the same local playlist.
    importer.tracks = [
        importer.tracks[1],
        DescribedTrack(
            "Rosanna", "Toto", duration=331, external_ids={"spotify": "s-ro"}
        ),
        importer.tracks[0],
    ]
    source.fetched.clear()
    job2 = asyncio.run(run("p1"))
    assert source.fetched == ["ro"]
    assert job2["playlist_id"] == job["playlist_id"]
    playlist = library.get_playlist(job["playlist_id"], with_tracks=True)
    assert [t["title"] for t in playlist["tracks"]] == ["Dreams", "Rosanna", "Africa"]


def test_review_accept_downloads_into_the_same_row(data_dir, source):
    row = _track(
        "Obscure",
        "Somebody",
        status="needs_review",
        candidates=[{"source": "fake", "ref": "ob", "title": "Obscure"}],
    )
    playlist = library.create_playlist("P")
    library.add_to_playlist(playlist["id"], [row["id"]])

    async def run():
        await jobs.review(row["id"], accept=True)
        await _drain()

    asyncio.run(run())
    done = library.get_track(row["id"])
    assert (
        done["status"] == "ready"
        and done["source_ref"] == "ob"
        and done["title"] == "Obscure"
    )
    assert library.playlist_track_ids(playlist["id"]) == [row["id"]]


def test_spotify_withheld_items_degrade_to_a_note(monkeypatch):
    from backend.modules.music.importers import spotify

    async def fake_token():
        return "t"

    async def fake_get(url, token, params=None):
        if url.endswith("/items"):
            raise spotify.SpotifyHTTPError("Spotify returned 403: Forbidden", 403)
        return {"name": "Someone Else's Mix"}

    monkeypatch.setattr(spotify, "_token", fake_token)
    monkeypatch.setattr(spotify, "_get", fake_get)
    playlist = asyncio.run(spotify.SpotifyImporter().read_playlist("abc"))
    assert playlist.tracks == [] and "own" in playlist.note
    assert playlist.origin == "spotify:abc"


def test_spotify_track_shapes_old_and_new():
    from backend.modules.music.importers import spotify

    obj = {
        "type": "track",
        "id": "t1",
        "name": "Africa",
        "artists": [{"name": "Toto"}],
        "album": {"name": "Toto IV", "images": [{"url": "https://i.scdn.co/x"}]},
        "duration_ms": 295000,
        "external_ids": {"isrc": "US1"},
    }
    for item in ({"item": obj}, {"track": obj}):
        desc = spotify._describe(spotify._item_track(item))
        assert (desc.title, desc.artist, desc.album, desc.duration) == (
            "Africa",
            "Toto",
            "Toto IV",
            295.0,
        )
        assert (
            desc.external_ids == {"spotify": "t1", "isrc": "US1"}
            and desc.art_url == "https://i.scdn.co/x"
        )
    assert spotify._describe({"type": "episode", "name": "Podcast"}) is None


# --- session ---------------------------------------------------------------------


def _three():
    return [_track(title=t) for t in ("A", "B", "C")]


def test_repeat_one_replays_and_repeat_all_cycles(data_dir, session):
    a, b, _c = _three()

    async def run():
        await session.add([a, b], mode="replace")
        await session.set_repeat("one")
        await session.ended()
        assert session.snapshot().now_playing.title == "A"
        await session.set_repeat("all")
        await session.ended()
        assert session.snapshot().now_playing.title == "B"
        await session.ended()
        assert session.snapshot().now_playing.title == "A"

    asyncio.run(run())


def test_previous_restarts_late_and_goes_back_early(data_dir, session):
    a, b, _ = _three()

    async def run():
        await session.add([a, b], mode="replace")
        await session.next()
        assert session.snapshot().now_playing.title == "B"
        await session.report_progress(10.0, 200.0)
        await session.previous()
        state = session.snapshot()
        assert state.now_playing.title == "B" and state.position == 0.0
        await session.previous()
        state = session.snapshot()
        assert state.now_playing.title == "A"
        assert [e.title for e in state.queue] == ["B"]

    asyncio.run(run())


def test_shuffle_keeps_every_entry(data_dir, session):
    tracks = [_track(title=str(i)) for i in range(12)]

    async def run():
        await session.add(tracks, mode="replace")
        before = [e.title for e in session.snapshot().queue]
        await session.set_shuffle(True)
        after = [e.title for e in session.snapshot().queue]
        assert sorted(after) == sorted(before)

    asyncio.run(run())


def test_session_persists_paused_and_drops_deleted_tracks(data_dir, session):
    a, b, c = _three()

    async def run():
        await session.add([a, b, c], mode="replace")
        await session.set_volume(0.3)
        await session.set_repeat("all")

    asyncio.run(run())
    library.delete_track(c["id"])
    restored = MusicSession()
    restored.load()
    state = restored.snapshot()
    assert state.now_playing.title == "A" and state.playing is False
    assert [e.title for e in state.queue] == ["B"]
    assert state.volume == 0.3 and state.repeat == "all"


def test_plays_are_recorded_with_listened_time_and_skips(
    data_dir, session, monkeypatch
):
    a, b, _ = _three()
    clock = iter(float(i) for i in range(100))
    monkeypatch.setattr(
        "backend.modules.music.session.time.monotonic", lambda: next(clock)
    )

    async def run():
        await session.add([a, b], mode="replace")
        for pos in range(5):
            await session.report_progress(float(pos), 200.0)
        await session.next()

    asyncio.run(run())
    plays = library.recent_plays()
    assert plays[0]["title"] == "A" and plays[0]["skipped"] == 1
    assert 3.0 <= plays[0]["played_seconds"] <= 5.0
    assert library.get_track(a["id"])["skip_count"] == 1


# --- scrobbling ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration", "listened", "expected"),
    [
        (25, 25, False),
        (200, 99, False),
        (200, 100, True),
        (1200, 239, False),
        (1200, 240, True),
        (None, 500, False),
    ],
)
def test_scrobble_threshold(duration, listened, expected):
    assert music_listeners.should_scrobble(duration, listened) is expected


class FakeListener:
    id = "fakefm"
    label = "FakeFM"

    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.sent: list[tuple[str, int]] = []

    def configured(self) -> bool:
        return True

    async def now_playing(self, track):
        return None

    async def scrobble(self, track, started_at):
        if self.fail:
            raise ProviderError("offline")
        self.sent.append((track.title, started_at))

    async def similar(self, seed, limit=20):
        return []


def test_failed_scrobbles_queue_and_flush(data_dir, monkeypatch):
    listener = FakeListener(fail=True)
    monkeypatch.setattr(listeners, "_items", {"fakefm": listener})
    monkeypatch.setattr(music_listeners, "scrobbling_enabled", lambda: True)
    track = _track("Africa", "Toto", duration=295.0)
    asyncio.run(music_listeners.on_finished(track, 1_700_000_000, 200.0, True, False))
    assert music_listeners.pending_count() == 1
    listener.fail = False
    assert asyncio.run(music_listeners.flush_queue()) == 1
    assert (
        listener.sent == [("Africa", 1_700_000_000)]
        and music_listeners.pending_count() == 0
    )


def test_lastfm_signature_is_sorted_md5():
    from backend.modules.music.listeners.lastfm import sign

    # Worked example: md5("api_keyxxxmethodauth.getSessiontokenyyy" + "secret")
    import hashlib

    want = hashlib.md5(b"api_keyxxxmethodauth.getSessiontokenyyysecret").hexdigest()  # noqa: S324
    assert (
        sign(
            {
                "token": "yyy",
                "method": "auth.getSession",
                "api_key": "xxx",
                "format": "json",
            },
            "secret",
        )
        == want
    )


# --- enrichment and lyrics -------------------------------------------------------------


def test_enrich_renames_youtube_titles_but_respects_locks_and_importers():
    found = {
        "title": "Africa",
        "artist": "Toto",
        "album": "Toto IV",
        "year": "1982",
        "mbid": "m1",
        "isrc": None,
    }
    youtube = {
        "title": "Africa (Official HD Video)",
        "artist": "TotoVEVO",
        "source": "youtube",
        "external_ids": {"youtube": "x"},
    }
    plan = enrich.plan_updates(youtube, found)
    assert (
        plan["title"] == "Africa"
        and plan["artist"] == "Toto"
        and plan["album"] == "Toto IV"
    )
    assert plan["external_ids"] == {"youtube": "x", "mbid": "m1"}

    locked = {**youtube, "locked_fields": ["title"]}
    assert "title" not in enrich.plan_updates(locked, found)

    # A different song entirely: never renamed, even on a confident lookup.
    other = {**youtube, "title": "Rosanna (Live)"}
    assert "title" not in enrich.plan_updates(other, found)

    spotify = {
        "title": "Africa",
        "artist": "Toto",
        "album": "",
        "source": "youtube",
        "external_ids": {"spotify": "s"},
    }
    plan = enrich.plan_updates(spotify, {**found, "artist": "TOTO"})
    assert "artist" not in plan and plan["album"] == "Toto IV"


def test_parse_lrc_handles_repeats_and_metadata():
    text = "[ar:Toto]\n[00:10.00][01:10.00]Chorus\n[00:05.5]Verse\nno stamp\n"
    assert lyrics.parse_lrc(text) == [
        [5.5, "Verse"],
        [10.0, "Chorus"],
        [70.0, "Chorus"],
    ]


# --- agent tools -----------------------------------------------------------------------


def test_every_music_tool_is_grouped():
    for tool in agent_tools._TOOLS:
        assert tool.name.startswith("music.") and tool.group == "music"
    assert len(agent_tools._TOOLS) == 7


def test_play_prefers_the_library_and_strips_filler(data_dir, session):
    _track("Africa", "Toto")
    _track("Hello", "Adele")
    out = asyncio.run(agent_tools._play({"query": "Africa by Toto"}))
    assert out["source"] == "library" and out["queued"]["title"] == "Africa"
    out = asyncio.run(
        agent_tools._play({"query": "something by Adele", "mode": "queue"})
    )
    assert out["queued"]["title"] == "Hello"
    state = session.snapshot()
    assert state.now_playing.title == "Africa" and [e.title for e in state.queue] == [
        "Hello"
    ]


def test_play_fetches_when_missing_and_asks_when_unsure(data_dir, session, source):
    source.catalog = {"Rosanna": [_cand("ro", "Rosanna", "Toto", 331)]}
    out = asyncio.run(agent_tools._play({"query": "Toto - Rosanna"}))
    assert out["source"] == "fake" and out["queued"]["title"] == "Rosanna"

    source.catalog = {
        "Mystery": [
            TrackCandidate(
                source="fake",
                ref="z",
                title="Unrelated Podcast",
                artist="",
                duration=3600,
            )
        ]
    }
    out = asyncio.run(agent_tools._play({"query": "Mystery Song by Nobody"}))
    assert "options" in out and "error" in out


def test_playlist_tools_round_trip(data_dir, session):
    a, b, _ = _three()
    out = asyncio.run(
        agent_tools._playlist(
            {"action": "create", "name": "Faves", "track_ids": [a["id"]]}
        )
    )
    assert out["tracks"] == 1
    asyncio.run(
        agent_tools._playlist(
            {"action": "add", "name": "faves", "track_ids": [b["id"], a["id"]]}
        )
    )
    listing = asyncio.run(agent_tools._library({"view": "playlist", "name": "Faves"}))
    assert [t["title"] for t in listing["tracks"]] == ["A", "B"]
    asyncio.run(agent_tools._playlist({"action": "play", "name": "Faves"}))
    assert session.snapshot().now_playing.title == "A"


def test_top_view_counts_unskipped_plays_in_the_window(data_dir):
    a, b, _ = _three()
    now = library.now_iso()
    for _ in range(3):
        library.record_play(
            a["id"], started_at=now, played_seconds=100, completed=True, skipped=False
        )
    library.record_play(
        b["id"], started_at=now, played_seconds=5, completed=False, skipped=True
    )
    out = asyncio.run(agent_tools._library({"view": "top", "period": "week"}))
    assert [(t["title"], t["plays_in_period"]) for t in out["tracks"]] == [("A", 3)]


def test_dj_prefers_library_matches_from_similarity(data_dir, session, monkeypatch):
    seed = _track("Africa", "Toto", play_count=5)
    _track("Dreams", "Fleetwood Mac")
    _track("Hold the Line", "Toto")

    class Similar(FakeListener):
        async def similar(self, seed, limit=20):
            return [
                DescribedTrack("Dreams", "Fleetwood Mac"),
                DescribedTrack("Hold the Line", "Toto"),
            ]

    monkeypatch.setattr(listeners, "_items", {"sim": Similar(fail=False)})
    result = asyncio.run(
        curator.curate(
            seeds=[seed], count=5, allow_download=False
        )
    )
    assert sorted(t["title"] for t in result["picks"]) == ["Dreams", "Hold the Line"]
    assert all("in your library" in w for w in result["why"])


def test_status_reports_review_and_imports(data_dir, session):
    _track("x", status="needs_review")
    out = asyncio.run(agent_tools._status({}))
    assert out["needs_review"] == 1 and out["now_playing"] is None
    assert json.dumps(out)  # serialisable for the model


# --- plugin SDK ----------------------------------------------------------------------


def test_example_folder_source_plugin_plugs_in(data_dir, tmp_path, monkeypatch, session):
    """`host.add_music_source` is the whole integration: the agent's `music.play`
    reaches a plugin source with no new tools."""
    from backend.sdk import PluginRegistry, load_plugins

    examples = Path(__file__).resolve().parent.parent.parent / "examples" / "backend-plugins"
    shelf = tmp_path / "nas"
    _wav(shelf / "Toto" / "Toto - Georgy Porgy.wav", tone=9)
    monkeypatch.setenv("MUSIC_FOLDER_SOURCE_DIR", str(shelf))
    reg = PluginRegistry()
    load_plugins(extra_dirs=[examples], reg=reg)
    assert reg.errors == [] and "folder" in reg.music_sources
    assert sources.get("folder") is not None

    async def run():
        out = await agent_tools._play({"query": "Georgy Porgy by Toto"})
        await _drain()
        return out

    out = asyncio.run(run())
    assert out["source"] == "folder"
    track = library.get_track(out["queued"]["track_id"])
    assert track["status"] == "ready" and track["artist"] == "Toto"


def test_only_one_client_owns_the_output(data_dir, session, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("backend.modules.music.session.time.monotonic", lambda: clock[0])
    a, *_ = _three()

    async def run():
        await session.add([a], mode="play")
        assert await session.claim_output("win1")
        # A second window can't take over a live owner without force...
        assert not await session.claim_output("win2")
        # ...and its progress and "ended" reports are ignored.
        await session.report_progress(50.0, 200.0, "win2")
        assert session.snapshot().position == 0.0
        assert not session.is_output("win2")
        # Once the owner goes quiet while playing, it can.
        clock[0] += 30
        assert await session.claim_output("win2")
        # A person pressing play in win1 takes it back.
        assert await session.claim_output("win1", force=True)
        assert session.snapshot().output_client == "win1"

    asyncio.run(run())
    restored = MusicSession()
    restored.load()
    assert restored.snapshot().output_client is None


def test_uploaded_audio_is_imported_and_staging_cleaned(data_dir, tmp_path):
    song = _wav(tmp_path / "Toto - Africa.wav", tone=3)
    client = _client()
    res = client.post(
        "/api/music/upload",
        files=[
            ("files", ("Toto - Africa.wav", song.read_bytes(), "audio/wav")),
            ("files", ("notes.txt", b"hello", "text/plain")),
        ],
    )
    body = res.json()
    assert res.status_code == 200
    assert [t["title"] for t in body["imported"]] == ["Africa"]
    assert body["failed"] == ["notes.txt: not an audio file"]
    assert not any(p.name.startswith("upload-") for p in library.music_dir().iterdir())


def test_lyrics_from_another_cut_keep_words_but_drop_timing(data_dir, monkeypatch):
    track = _track("Africa", "Toto", duration=271.0, album="Magic Moments")

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            if url == lyrics.LRCLIB_URL:
                return Resp(404, {})
            return Resp(200, [{"duration": 295, "plainLyrics": "I hear the drums", "syncedLyrics": "[00:10.00]I hear the drums"}])

    monkeypatch.setattr(lyrics.httpx, "AsyncClient", Client)
    found = asyncio.run(lyrics.get_lyrics(track["id"]))
    assert found["plain"] == "I hear the drums" and found["synced"] == []
