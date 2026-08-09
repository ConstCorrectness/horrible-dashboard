"""Tests for the karaoke module: the songs catalog, the shared session's queue and
transport semantics, byte-range media serving, and the agent tools.

Nothing here touches the network. yt-dlp is stubbed at the `downloader` seam and
ffmpeg is never spawned — what's under test is the *state machine*, which is where
this module's real complexity is.
"""

import asyncio

import pytest
from fastapi import HTTPException

from backend.modules.karaoke import agent_tools, downloader, store, transpose
from backend.modules.karaoke.models import (
    AddToQueueRequest,
    DownloadRequest,
    SearchResult,
)
from backend.modules.karaoke.routes import add_to_queue, download, media
from backend.modules.karaoke.session import KaraokeSession


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolated app.db + songs dir per test."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_broadcast(monkeypatch):
    """Swallow `/ws` fan-out — there are no connections in a unit test."""

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("backend.modules.karaoke.session.broadcast_event", _noop)


@pytest.fixture
def session():
    """A fresh session per test. The production one is process-global on purpose,
    which is exactly why tests must not share it."""
    return KaraokeSession()


def _song(title="Test Song", artist="", video_id="", status="ready"):
    return store.create_song(
        title=title, artist=artist, video_id=video_id, status=status
    )


# --- store ---


def test_song_roundtrip(data_dir):
    song = _song(title="Africa", artist="Toto", video_id="abc12345678")
    fetched = store.get_song(song["id"])
    assert fetched is not None
    assert fetched["title"] == "Africa"
    assert fetched["artist"] == "Toto"
    assert fetched["status"] == "ready"


def test_find_by_video_id_ignores_failed_rows(data_dir):
    """A failed download must not read as 'we have this' — the UI would offer to
    queue a song with no file behind it."""
    _song(title="Broken", video_id="vid00000001", status="failed")
    assert store.find_by_video_id("vid00000001") is None

    ready = _song(title="Fine", video_id="vid00000001", status="ready")
    found = store.find_by_video_id("vid00000001")
    assert found is not None
    assert found["id"] == ready["id"]


def test_song_path_rejects_traversal(data_dir):
    """`filename` comes from yt-dlp's chosen extension; a row must never be able
    to address a file outside the songs directory."""
    song = _song()
    store.update_song(song["id"], filename="../../etc/passwd")
    assert store.song_path(store.get_song(song["id"])) is None

    store.update_song(song["id"], filename="ok.mp4")
    resolved = store.song_path(store.get_song(song["id"]))
    assert resolved is not None
    assert resolved.parent == store.songs_dir().resolve()


def test_list_songs_search_matches_artist(data_dir):
    _song(title="Dancing Queen", artist="ABBA")
    _song(title="Africa", artist="Toto")
    assert [s["title"] for s in store.list_songs("abba")] == ["Dancing Queen"]


def test_delete_song_removes_file(data_dir):
    song = _song()
    path = store.songs_dir() / "gone.mp4"
    path.write_bytes(b"x")
    store.update_song(song["id"], filename="gone.mp4")

    assert store.delete_song(song["id"]) is True
    assert not path.exists()
    assert store.get_song(song["id"]) is None
    assert store.delete_song(song["id"]) is False


# --- session: queue semantics ---


def test_same_song_queues_twice_as_distinct_entries(data_dir, session):
    """The central modelling rule: entries, not songs. Keying the queue by song id
    would make 'add it again for the next singer' a silent no-op."""
    song = _song()
    session._autoplay = False  # don't auto-start; we're inspecting the queue
    a = asyncio.run(session.add(song, singer="Ana"))
    b = asyncio.run(session.add(song, singer="Ben"))

    assert a.entry_id != b.entry_id
    assert [e.singer for e in session.snapshot().queue] == ["Ana", "Ben"]


def test_add_next_jumps_the_line(data_dir, session):
    song = _song()
    session._autoplay = False
    asyncio.run(session.add(song, singer="Ana"))
    asyncio.run(session.add(song, singer="Ben", next_up=True))
    assert [e.singer for e in session.snapshot().queue] == ["Ben", "Ana"]


def test_first_add_starts_playing_when_idle(data_dir, session):
    """Tapping the first song of the night should just start it, without the host
    walking over to press play."""
    song = _song(title="Opener")
    asyncio.run(session.add(song, singer="Ana"))

    state = session.snapshot()
    assert state.now_playing is not None
    assert state.now_playing.title == "Opener"
    assert state.playing is True
    assert state.queue == []


def test_autoplay_off_does_not_auto_start(data_dir, session):
    song = _song()
    asyncio.run(session.set_autoplay(False))
    asyncio.run(session.add(song))
    assert session.snapshot().now_playing is None
    assert len(session.snapshot().queue) == 1


def test_next_song_retires_current_into_history(data_dir, session):
    first, second = _song(title="One"), _song(title="Two")
    asyncio.run(session.add(first))  # auto-starts
    asyncio.run(session.add(second))

    asyncio.run(session.next_song())
    state = session.snapshot()
    assert state.now_playing is not None
    assert state.now_playing.title == "Two"
    assert [e.title for e in state.history] == ["One"]
    assert state.history[0].played_at is not None


def test_next_song_on_empty_queue_clears_the_screen(data_dir, session):
    asyncio.run(session.add(_song(title="Only")))
    asyncio.run(session.next_song())
    state = session.snapshot()
    assert state.now_playing is None
    assert state.playing is False


def test_remove_and_move(data_dir, session):
    session._autoplay = False
    entries = [asyncio.run(session.add(_song(title=f"S{i}"))) for i in range(3)]

    assert asyncio.run(session.move(entries[2].entry_id, 0)) is True
    assert [e.title for e in session.snapshot().queue] == ["S2", "S0", "S1"]

    # Past the end clamps rather than failing: a drag off the bottom of the list
    # means "put it last", not "error".
    assert asyncio.run(session.move(entries[2].entry_id, 99)) is True
    assert [e.title for e in session.snapshot().queue] == ["S0", "S1", "S2"]

    assert asyncio.run(session.remove(entries[1].entry_id)) is True
    assert [e.title for e in session.snapshot().queue] == ["S0", "S2"]
    assert asyncio.run(session.remove("nope")) is False
    assert asyncio.run(session.move("nope", 0)) is False


def test_play_with_nothing_loaded_pulls_from_the_queue(data_dir, session):
    session._autoplay = False
    asyncio.run(session.add(_song(title="Waiting")))
    asyncio.run(session.set_playing(True))
    assert session.snapshot().now_playing.title == "Waiting"


def test_play_with_nothing_at_all_stays_stopped(data_dir, session):
    """`playing = True` with nothing loaded would leave every client rendering a
    transport that claims to be playing silence."""
    asyncio.run(session.set_playing(True))
    assert session.snapshot().playing is False


def test_song_ended_respects_autoplay(data_dir, session):
    # Staged, not seeded via `_autoplay`: `add` auto-starts the first song, so
    # queueing A then B with autoplay on leaves A playing and B waiting — exactly
    # the state a real "the video ran out" event fires in.
    asyncio.run(session.add(_song(title="A")))
    asyncio.run(session.add(_song(title="B")))
    assert session.snapshot().now_playing.title == "A"

    asyncio.run(session.song_ended())
    assert session.snapshot().now_playing.title == "B"

    # With autoplay off, the end of a song clears the screen instead of pulling
    # the next singer up.
    asyncio.run(session.add(_song(title="C")))
    asyncio.run(session.set_autoplay(False))
    asyncio.run(session.song_ended())
    assert session.snapshot().now_playing is None
    assert [e.title for e in session.snapshot().queue] == ["C"]


def test_entry_queued_while_downloading_is_not_ready(data_dir, session):
    """The stage must be able to tell 'no file yet' from 'ready'. A `<video>`
    pointed at a not-yet-downloaded song 404s and never retries."""
    song = _song(title="Still coming", status="queued")
    asyncio.run(session.add(song, singer="Ana"))
    assert session.snapshot().now_playing.ready is False


def test_finished_download_unblocks_the_waiting_entry(data_dir, session):
    """The regression this whole flag exists for: a song queued mid-download used
    to reach the stage, fail to load, and stay black forever once the file landed,
    because nothing re-triggered the load."""
    song = _song(title="Still coming", status="queued")
    asyncio.run(session.add(song, singer="Ana"))
    assert session.snapshot().now_playing.ready is False

    store.update_song(song["id"], status="ready", filename="x.mp4")
    asyncio.run(session.song_downloaded(song["id"], ok=True))

    playing = session.snapshot().now_playing
    assert playing.ready is True
    assert playing.title == "Still coming"


def test_finished_download_unblocks_entries_still_in_the_queue(data_dir, session):
    """Not just the playing one: the same song can be queued for several singers,
    and every waiting entry has to be released."""
    session._autoplay = False
    song = _song(title="Shared", status="queued")
    asyncio.run(session.add(song, singer="Ana"))
    asyncio.run(session.add(song, singer="Ben"))
    assert [e.ready for e in session.snapshot().queue] == [False, False]

    asyncio.run(session.song_downloaded(song["id"], ok=True))
    assert [e.ready for e in session.snapshot().queue] == [True, True]


def test_failed_download_advances_past_the_stranded_entry(data_dir, session):
    """A download that never arrives must not wedge the stage on a song that
    cannot play — the room would just stare at it."""
    dead = _song(title="Never arrives", status="queued")
    good = _song(title="Fine", status="ready")
    asyncio.run(session.add(dead, singer="Ana"))
    asyncio.run(session.add(good, singer="Ben"))
    assert session.snapshot().now_playing.title == "Never arrives"

    asyncio.run(session.song_downloaded(dead["id"], ok=False))
    state = session.snapshot()
    assert state.now_playing.title == "Fine"
    assert all(e.title != "Never arrives" for e in state.queue)


def test_failed_download_drops_queued_entries_without_disturbing_playback(
    data_dir, session
):
    playing = _song(title="Playing", status="ready")
    dead = _song(title="Never arrives", status="queued")
    asyncio.run(session.add(playing))
    asyncio.run(session.add(dead, singer="Ana"))

    asyncio.run(session.song_downloaded(dead["id"], ok=False))
    state = session.snapshot()
    assert state.now_playing.title == "Playing"
    assert state.queue == []


def test_song_downloaded_is_a_no_op_for_unqueued_songs(data_dir, session):
    """Downloads happen without anything waiting on them all the time (the plain
    'get it for later' path); that must not touch the session."""
    asyncio.run(session.add(_song(title="Playing", status="ready")))
    before = session.snapshot().revision
    asyncio.run(session.song_downloaded(_song(status="queued")["id"], ok=True))
    assert session.snapshot().revision == before


def test_library_queued_song_is_ready_immediately(data_dir, session):
    asyncio.run(session.add(_song(title="On disk", status="ready")))
    assert session.snapshot().now_playing.ready is True


def test_completed_download_wires_through_to_the_playing_entry(
    data_dir, monkeypatch, session
):
    """End-to-end wiring of the reported bug, with the network stubbed out.

    Queue-while-downloading → the entry reaches the stage unplayable → the real
    `download_song` finishes → the entry flips to ready. The unit tests above cover
    `song_downloaded`; this one covers the thing that was actually missing, which
    was nobody *calling* it.
    """
    monkeypatch.setattr(
        "backend.modules.karaoke.session.session", session, raising=False
    )
    song = _song(title="Arrives late", status="queued")

    def fake_fetch(song_id, url):
        (store.songs_dir() / f"{song_id}.mp4").write_bytes(b"video bytes")
        return {"ext": "mp4", "title": "Arrives late", "duration": 100}

    monkeypatch.setattr(downloader, "_download_blocking", fake_fetch)

    asyncio.run(session.add(song, singer="Ana"))
    assert session.snapshot().now_playing.ready is False

    asyncio.run(downloader.download_song(song["id"], "https://youtu.be/aaaaaaaaaaa"))

    playing = session.snapshot().now_playing
    assert playing.ready is True
    assert playing.title == "Arrives late"
    assert store.get_song(song["id"])["status"] == "ready"


def test_download_replaces_the_url_placeholder_title(data_dir, monkeypatch, session):
    """A caller that supplies no title gets the URL as a placeholder (the column is
    NOT NULL and the row must show something while it downloads). yt-dlp's real
    title has to win over it, or the library shows a URL forever."""
    monkeypatch.setattr(
        "backend.modules.karaoke.session.session", session, raising=False
    )
    url = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    song = store.create_song(title=url, url=url, status="queued")

    def fake_fetch(song_id, _url):
        (store.songs_dir() / f"{song_id}.mp4").write_bytes(b"v")
        return {"ext": "mp4", "title": "Toto - Africa (Karaoke)", "duration": 100}

    monkeypatch.setattr(downloader, "_download_blocking", fake_fetch)
    asyncio.run(downloader.download_song(song["id"], url))

    row = store.get_song(song["id"])
    assert row["title"] == "Africa (Karaoke)"
    assert row["artist"] == "Toto"


def test_download_keeps_a_caller_supplied_title(data_dir, monkeypatch, session):
    """The other side of it: an explicit title from the UI or the agent must not
    be overwritten by yt-dlp's."""
    monkeypatch.setattr(
        "backend.modules.karaoke.session.session", session, raising=False
    )
    song = store.create_song(
        title="My chosen title", url="https://youtu.be/aaaaaaaaaaa", status="queued"
    )

    def fake_fetch(song_id, _url):
        (store.songs_dir() / f"{song_id}.mp4").write_bytes(b"v")
        return {"ext": "mp4", "title": "Something Else Entirely", "duration": 10}

    monkeypatch.setattr(downloader, "_download_blocking", fake_fetch)
    asyncio.run(downloader.download_song(song["id"], "https://youtu.be/aaaaaaaaaaa"))

    assert store.get_song(song["id"])["title"] == "My chosen title"


def test_failed_download_wires_through_and_clears_the_stage(
    data_dir, monkeypatch, session
):
    monkeypatch.setattr(
        "backend.modules.karaoke.session.session", session, raising=False
    )
    song = _song(title="Never arrives", status="queued")

    def boom(song_id, url):
        raise RuntimeError("video unavailable")

    monkeypatch.setattr(downloader, "_download_blocking", boom)

    asyncio.run(session.add(song, singer="Ana"))
    asyncio.run(downloader.download_song(song["id"], "https://youtu.be/aaaaaaaaaaa"))

    # The stage must not be left holding a song whose file will never exist.
    assert session.snapshot().now_playing is None
    assert store.get_song(song["id"])["status"] == "failed"


def test_revision_increases_on_every_mutation(data_dir, session):
    """Clients drop broadcasts older than what they hold; that only works if the
    revision moves on every change."""
    start = session.snapshot().revision
    asyncio.run(session.set_volume(0.5))
    asyncio.run(session.set_semitones(2))
    assert session.snapshot().revision > start


def test_transpose_and_volume_clamp(data_dir, session):
    asyncio.run(session.set_semitones(99))
    assert session.snapshot().semitones == 6
    asyncio.run(session.set_semitones(-99))
    assert session.snapshot().semitones == -6
    asyncio.run(session.set_volume(5.0))
    assert session.snapshot().volume == 1.0


def test_progress_does_not_bump_revision(data_dir, session):
    """It fires once a second from the stage; if it counted as a state change the
    whole queue would be rebroadcast at 1 Hz."""
    asyncio.run(session.add(_song()))
    before = session.snapshot().revision
    asyncio.run(session.report_progress(42.0, 180.0))
    after = session.snapshot()
    assert after.revision == before
    assert after.position == 42.0
    assert after.duration == 180.0


def test_stop_keeps_the_queue(data_dir, session):
    session._autoplay = False
    asyncio.run(session.add(_song(title="A")))
    asyncio.run(session.add(_song(title="B")))
    asyncio.run(session.set_playing(True))

    asyncio.run(session.stop())
    state = session.snapshot()
    assert state.now_playing is None
    assert [e.title for e in state.queue] == ["B"]


# --- routes ---


def test_download_reuses_an_existing_copy(data_dir, monkeypatch):
    """Tapping a search result twice must not download twice — and must still
    honour the queue request."""
    existing = _song(title="Have it", video_id="dup00000001")
    queued: list[str] = []

    async def fake_add(song, singer="", next_up=False):
        queued.append(song["id"])

    monkeypatch.setattr("backend.modules.karaoke.routes.session.add", fake_add)
    monkeypatch.setattr(
        downloader,
        "start_download",
        lambda *a: pytest.fail("should not download an existing song"),
    )

    result = asyncio.run(
        download(DownloadRequest(video_id="dup00000001", queue_for="Ana"))
    )
    assert result.id == existing["id"]
    assert queued == [existing["id"]]


def test_download_without_ytdlp_is_a_503(data_dir, monkeypatch):
    monkeypatch.setattr(downloader, "available", lambda: False)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(download(DownloadRequest(url="https://youtu.be/aaaaaaaaaaa")))
    assert excinfo.value.status_code == 503


def test_download_queues_before_the_file_arrives(data_dir, monkeypatch):
    """The entry holds a place in the running order while the file downloads —
    waiting would push a guest's song behind ones queued after it."""
    started: list[str] = []
    monkeypatch.setattr(downloader, "available", lambda: True)
    monkeypatch.setattr(
        downloader, "start_download", lambda song_id, url: started.append(song_id)
    )

    song = asyncio.run(
        download(DownloadRequest(url="https://youtu.be/bbbbbbbbbbb", queue_for="Ben"))
    )
    assert song.status == "queued"
    assert started == [song.id]


def test_add_to_queue_404s_for_an_unknown_song(data_dir):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(add_to_queue(AddToQueueRequest(song_id="nope")))
    assert excinfo.value.status_code == 404


class _Request:
    """The two attributes the media route actually reads."""

    def __init__(self, range_header=None):
        self.headers = {"range": range_header} if range_header else {}


def _playable(data: bytes) -> str:
    song = _song()
    path = store.songs_dir() / f"{song['id']}.mp4"
    path.write_bytes(data)
    store.update_song(song["id"], filename=path.name)
    return song["id"]


def test_media_serves_a_byte_range(data_dir):
    song_id = _playable(b"0123456789")
    response = asyncio.run(media(song_id, _Request("bytes=2-5")))
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"


def test_media_suffix_range_serves_the_tail(data_dir):
    """`bytes=-3` asks for the LAST three bytes. Reading the number as a start
    offset is the classic misread and serves the wrong part of the file."""
    song_id = _playable(b"0123456789")
    response = asyncio.run(media(song_id, _Request("bytes=-3")))
    assert response.headers["content-range"] == "bytes 7-9/10"


def test_media_open_ended_range_runs_to_eof(data_dir):
    song_id = _playable(b"0123456789")
    response = asyncio.run(media(song_id, _Request("bytes=8-")))
    assert response.headers["content-range"] == "bytes 8-9/10"


def test_media_unsatisfiable_range_is_416(data_dir):
    song_id = _playable(b"0123456789")
    response = asyncio.run(media(song_id, _Request("bytes=99-")))
    assert response.status_code == 416


def test_media_without_a_file_is_404(data_dir):
    song = _song()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(media(song["id"], _Request()))
    assert excinfo.value.status_code == 404


def test_media_transpose_needs_ffmpeg(data_dir, monkeypatch):
    song_id = _playable(b"0123456789")
    monkeypatch.setattr(transpose, "available", lambda: False)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(media(song_id, _Request(), semitones=2))
    assert excinfo.value.status_code == 503


# --- transpose command ---


def test_transpose_chain_includes_atempo(tmp_path):
    """Dropping the `atempo` stage is the classic pitch-shift bug: the song comes
    out higher *and* faster."""
    command = transpose.build_command(tmp_path / "x.mp4", 2)
    chain = command[command.index("-af") + 1]
    assert "asetrate" in chain
    assert "aresample" in chain
    assert "atempo" in chain
    # Up two semitones plays faster, so it must be slowed back down.
    tempo = float(chain.split("atempo=")[1])
    assert tempo < 1.0


def test_transpose_chain_inverts_for_a_lower_key(tmp_path):
    chain = transpose.build_command(tmp_path / "x.mp4", -2)[
        transpose.build_command(tmp_path / "x.mp4", -2).index("-af") + 1
    ]
    assert float(chain.split("atempo=")[1]) > 1.0


def test_transpose_stays_inside_one_atempo_stage(tmp_path):
    """ffmpeg's atempo accepts 0.5-2.0 per instance. ±6 semitones is the model's
    clamp precisely so one stage is enough — a wider range would need chaining."""
    for semitones in (-6, 6):
        chain = transpose.build_command(tmp_path / "x.mp4", semitones)
        tempo = float(chain[chain.index("-af") + 1].split("atempo=")[1])
        assert 0.5 <= tempo <= 2.0


# --- downloader helpers ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://example.com/nope", ""),
    ],
)
def test_parse_video_id(raw, expected):
    assert downloader.parse_video_id(raw) == expected


def test_split_title_is_conservative():
    """A wrong artist is worse than none: it shows up in the library list and in
    every queue row."""
    assert downloader._split_title("Toto - Africa") == ("Toto", "Africa")
    assert downloader._split_title("Africa (Karaoke)") == ("", "Africa (Karaoke)")


# --- agent tools ---


def test_agent_queue_prefers_the_library(data_dir, monkeypatch, session):
    """Instant, already the right karaoke cut, and no bandwidth."""
    _song(title="Africa", artist="Toto")
    monkeypatch.setattr("backend.modules.karaoke.agent_tools.session", session)
    monkeypatch.setattr(
        downloader,
        "search",
        lambda *a, **k: pytest.fail("should not hit the network"),
    )

    result = asyncio.run(agent_tools._queue({"query": "Africa", "singer": "Ana"}))
    assert result["source"] == "library"
    assert result["queued"]["singer"] == "Ana"


def test_agent_queue_falls_back_to_youtube(data_dir, monkeypatch, session):
    async def fake_search(query, limit=1, karaoke_bias=True):
        return [
            SearchResult(
                video_id="new00000001",
                title="Dancing Queen (Karaoke)",
                url="https://youtu.be/new00000001",
            )
        ], ""

    started: list[str] = []
    monkeypatch.setattr("backend.modules.karaoke.agent_tools.session", session)
    monkeypatch.setattr(downloader, "available", lambda: True)
    monkeypatch.setattr(downloader, "search", fake_search)
    monkeypatch.setattr(
        downloader, "start_download", lambda song_id, url: started.append(song_id)
    )

    result = asyncio.run(agent_tools._queue({"query": "Dancing Queen"}))
    assert result["source"] == "youtube"
    assert result["downloading"] is True
    assert len(started) == 1
    assert session.snapshot().now_playing is not None


def test_agent_status_reports_the_room(data_dir, monkeypatch, session):
    monkeypatch.setattr("backend.modules.karaoke.agent_tools.session", session)
    asyncio.run(session.add(_song(title="Africa", artist="Toto"), singer="Ana"))

    status = asyncio.run(agent_tools._status({}))
    assert status["now_playing"]["title"] == "Africa"
    assert status["now_playing"]["singer"] == "Ana"
    assert status["playing"] is True


def test_agent_control_rejects_an_unknown_action(data_dir, monkeypatch, session):
    monkeypatch.setattr("backend.modules.karaoke.agent_tools.session", session)
    assert "error" in asyncio.run(agent_tools._control({"action": "explode"}))


def test_agent_control_drives_transport(data_dir, monkeypatch, session):
    monkeypatch.setattr("backend.modules.karaoke.agent_tools.session", session)
    asyncio.run(session.add(_song(title="A")))
    asyncio.run(session.add(_song(title="B")))

    asyncio.run(agent_tools._control({"action": "next"}))
    assert session.snapshot().now_playing.title == "B"

    asyncio.run(agent_tools._control({"action": "transpose", "value": -3}))
    assert session.snapshot().semitones == -3

    asyncio.run(agent_tools._control({"action": "pause"}))
    assert session.snapshot().playing is False


def test_agent_unqueue_needs_an_entry_id(data_dir, monkeypatch, session):
    monkeypatch.setattr("backend.modules.karaoke.agent_tools.session", session)
    assert "error" in asyncio.run(agent_tools._unqueue({}))
    assert "error" in asyncio.run(agent_tools._unqueue({"entry_id": "nope"}))


def test_agent_tools_are_grouped(data_dir):
    """Ungrouped backend tools are loaded on every agent turn and cost context
    unconditionally — this group must stay progressive."""
    assert all(tool.group == "karaoke" for tool in agent_tools._TOOLS)
