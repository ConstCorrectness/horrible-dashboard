"""Scrive's clip editor: edit lists, the ffmpeg command they compile to, and the jobs
that run it.

The compiler is pure, so the exact filtergraph is pinned. The rest runs real ffmpeg
on a three-second test video made with lavfi — skipped when ffmpeg is absent. Speech
to text is faked at the module seam: the real Whisper is the voice extra's business,
and what matters here is how the sound is cut into cues on the output timeline.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import extras
from backend.modules.scrive import clips, media, social, store

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")

HD = clips.Probe(duration=10.0, width=1920, height=1080, fps=30.0, has_audio=True)


@pytest.fixture
def pure(monkeypatch):
    """Compile without needing ffmpeg on PATH."""
    monkeypatch.setattr(clips, "ffmpeg", lambda: "ffmpeg")


def edit(**kw) -> clips.EditList:
    return clips.EditList.model_validate({"source": "media/demo.mp4", **kw})


# --- the compiler ---------------------------------------------------------------------


def test_render_graph_trims_joins_crops_scales_and_burns_captions(pure):
    plan = clips.compile_render(
        edit(
            segments=[{"in": 1, "out": 3}, {"in": 5.5, "out": 7.5}],
            crop={"aspect": "1:1", "x": 0.5, "y": 0.5},
            speed=2,
            captions=[{"t0": 0, "t1": 1.5, "text": "hello"}],
            audio={"gain": 0.5},
        ),
        HD,
        Path("/src/demo.mp4"),
        "out.mp4",
    )
    graph = plan.args[plan.args.index("-filter_complex") + 1]
    assert graph.split(";") == [
        "[0:v]trim=start=1:end=3,setpts=PTS-STARTPTS[v0]",
        "[0:v]trim=start=5.5:end=7.5,setpts=PTS-STARTPTS[v1]",
        "[v0][v1]concat=n=2:v=1:a=0,setpts=PTS/2,crop=1080:1080:420:0,"
        "scale=1080:1080:flags=lanczos,setsar=1,subtitles=captions.ass[vout]",
        "[0:a]atrim=start=1:end=3,asetpts=PTS-STARTPTS[a0]",
        "[0:a]atrim=start=5.5:end=7.5,asetpts=PTS-STARTPTS[a1]",
        "[a0][a1]concat=n=2:v=0:a=1,atempo=2,volume=0.5[aout]",
    ]
    assert plan.duration == 2.0
    assert plan.size == (1080, 1080)
    assert plan.args[plan.args.index("-t") + 1] == "2"
    assert ["-map", "[aout]"] == plan.args[plan.args.index("[aout]") - 1 :][:2]
    assert "libx264" in plan.args and plan.args[-1] == "out.mp4"
    assert (
        plan.ass is not None and "Dialogue: 0,0:00:00.00,0:00:01.50,Caption" in plan.ass
    )


def test_gif_is_a_palette_pass_with_no_sound(pure):
    plan = clips.compile_render(
        edit(
            segments=[{"in": 0, "out": 4}],
            output={"preset": "gif", "gif_fps": 10, "gif_width": 320},
        ),
        HD,
        Path("/src/demo.mp4"),
        "out.gif",
    )
    graph = plan.args[plan.args.index("-filter_complex") + 1]
    assert graph.endswith(
        "scale=320:180:flags=lanczos,setsar=1,fps=10,split[s0][s1];"
        "[s0]palettegen=stats_mode=diff[pal];"
        "[s1][pal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[vout]"
    )
    assert "[aout]" not in plan.args and "-an" not in plan.args
    assert plan.args[plan.args.index("-loop") + 1] == "0"


def test_replaced_audio_is_trimmed_to_the_clip_and_a_mute_drops_sound(pure):
    plan = clips.compile_render(
        edit(segments=[{"in": 0, "out": 3}], audio={"replace": "media/song.mp3"}),
        HD,
        Path("/src/demo.mp4"),
        "out.mp4",
        replace_audio=Path("/src/song.mp3"),
    )
    assert plan.args.count("-i") == 2 and str(Path("/src/song.mp3")) in plan.args
    graph = plan.args[plan.args.index("-filter_complex") + 1]
    assert "[1:a]atrim=end=3,asetpts=PTS-STARTPTS[aout]" in graph
    assert "[0:a]" not in graph

    muted = clips.compile_render(
        edit(segments=[{"in": 0, "out": 3}], audio={"mute": True}),
        HD,
        Path("/src/demo.mp4"),
        "out.mp4",
    )
    assert "-an" in muted.args and "[0:a]" not in str(muted.args)


def test_speed_beyond_atempo_range_chains_and_fps_is_capped(pure):
    assert clips._atempo(4) == ["atempo=2.0", "atempo=2"]
    assert clips._atempo(0.25) == ["atempo=0.5", "atempo=0.5"]
    assert clips._atempo(1) == []
    fast = HD.model_copy(update={"fps": 120.0})
    plan = clips.compile_render(
        edit(segments=[{"in": 0, "out": 1}]), fast, Path("/s.mp4"), "o.mp4"
    )
    assert "fps=60[vout]" in plan.args[plan.args.index("-filter_complex") + 1]


def test_unrenderable_edit_lists_are_refused(pure):
    with pytest.raises(clips.ClipError, match="no segments"):
        clips.compile_render(edit(), HD, Path("/s.mp4"), "o.mp4")
    with pytest.raises(clips.ClipError, match="empty"):
        clips.compile_render(
            edit(segments=[{"in": 2, "out": 2}]), HD, Path("/s.mp4"), "o.mp4"
        )
    with pytest.raises(clips.ClipError, match="after the source ends"):
        clips.compile_render(
            edit(segments=[{"in": 11, "out": 12}]), HD, Path("/s.mp4"), "o.mp4"
        )


# --- geometry -------------------------------------------------------------------------


def test_crop_window_is_the_largest_of_its_aspect_kept_inside_the_frame():
    vertical = clips.Crop(aspect="9:16")
    assert clips.crop_rect(vertical, 1920, 1080) == (606, 1080, 656, 0)
    assert clips.crop_rect(vertical.model_copy(update={"x": 0}), 1920, 1080)[2] == 0
    assert clips.crop_rect(vertical.model_copy(update={"x": 1}), 1920, 1080)[2] == 1314
    assert clips.crop_rect(clips.Crop(aspect="16:9"), 1080, 1920) == (1080, 606, 0, 656)
    assert clips.crop_rect(clips.Crop(), 1919, 1081) == (1918, 1080, 0, 0)


def test_output_is_fitted_to_the_preset_and_never_enlarged():
    uhd = HD.model_copy(update={"width": 3840, "height": 2160})
    assert clips.output_size(edit(), uhd) == (1920, 1080)
    assert clips.output_size(edit(output={"preset": "web"}), uhd) == (1280, 720)
    small = HD.model_copy(update={"width": 640, "height": 360})
    assert clips.output_size(edit(), small) == (640, 360)
    tall = edit(crop={"aspect": "9:16"}, output={"preset": "youtube"})
    assert clips.output_size(tall, uhd) == (1078, 1920)


# --- captions -------------------------------------------------------------------------


def test_ass_prints_text_as_typed_and_drops_cues_past_the_end():
    ass = clips.build_ass(
        edit(
            captions=[
                {"t0": 0, "t1": 2, "text": "set {x} to C:\\tmp\nsecond line"},
                {"t0": 9, "t1": 12, "text": "too late"},
                {"t0": 1, "t1": 1.5, "text": "   "},
            ],
            overlays=[{"t0": 0, "t1": 30, "text": "TITLE", "pos": "center"}],
        ),
        1280,
        720,
        duration=5,
    )
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert dialogue == [
        "Dialogue: 0,0:00:00.00,0:00:02.00,Caption,,0,0,0,,set (x) to C:\u2216tmp\\Nsecond line",
        "Dialogue: 1,0:00:00.00,0:00:05.00,Center,,0,0,0,,TITLE",
    ]
    assert "PlayResY: 720" in ass
    assert "Style: Center,Arial,50," in ass and ",0,5,36,36,36,1" in ass


def test_captions_not_burned_are_left_out_of_the_picture(pure):
    plan = clips.compile_render(
        edit(
            segments=[{"in": 0, "out": 2}],
            captions=[{"t0": 0, "t1": 1, "text": "hi"}],
            output={"burn_captions": False},
        ),
        HD,
        Path("/s.mp4"),
        "o.mp4",
    )
    assert plan.ass is None and "subtitles" not in str(plan.args)


def test_speech_is_cut_at_pauses_and_into_short_cues():
    log = (
        "[silencedetect @ 0x1] silence_start: 2.5\n"
        "[silencedetect @ 0x1] silence_end: 3.4 | silence_duration: 0.9\n"
        "[silencedetect @ 0x1] silence_start: 15.9\n"
        "[silencedetect @ 0x1] silence_end: 16.1 | silence_duration: 0.2\n"
        "[silencedetect @ 0x1] silence_start: 19\n"
    )
    silent = media.parse_silences(log)
    assert silent[0] == (2.5, 3.4) and silent[-1][1] == float("inf")
    regions = media.speech_regions(silent, 20.0)
    assert regions[0] == (0.0, 2.5)
    # 3.4..15.9 is 12.5 s of speech: three even cues of at most six seconds.
    assert [round(b - a, 3) for a, b in regions[1:4]] == [4.167] * 3
    assert regions[4] == pytest.approx((16.1, 19))
    assert all(b - a <= media.MAX_CUE_S for a, b in regions)
    # Speech shorter than MIN_CUE_S is no cue.
    assert media.speech_regions([(0.1, 10)], 10) == []


def test_findings_say_where_the_clip_will_not_go():
    long = edit(segments=[{"in": 0, "out": 10}] * 15)
    rules = {f.rule for f in clips.check(long, HD)}
    assert "duration" in rules  # 150 s is over X's 140
    assert clips.check(edit(), HD)[0].severity == "error"
    past = clips.check(edit(segments=[{"in": 8, "out": 12}]), HD)
    assert any("past the end" in f.message for f in past)
    assert clips.check(edit(), None)[0].rule == "source"


def test_render_lands_beside_the_clip_named_for_its_preset():
    e = edit(output={"preset": "x"})
    assert clips.output_path("media/demo.clip.json", e) == "media/demo.x.mp4"
    assert clips.output_path(
        "media/demo-2.clip.json", edit(output={"preset": "gif"})
    ) == ("media/demo-2.gif")
    same = clips.EditList(source="media/demo.gif", output=clips.Output(preset="gif"))
    assert clips.output_path("media/demo.clip.json", same) == "media/demo-edit.gif"


# --- real ffmpeg ----------------------------------------------------------------------


@pytest.fixture
def scrive_root(tmp_path) -> Path:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "scrive"
    (data_dir / "settings.json").write_text(
        json.dumps({"scrive.root": str(root), "scrive.semanticSearch": False})
    )
    return root


@pytest.fixture
def client(scrive_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


def _make_video(path: Path, seconds: float = 3) -> None:
    """A test pattern with a tone that drops out for the middle second."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
         "-af", "volume=enable='between(t,1,2)':volume=0",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, timeout=60,
    )  # fmt: skip


@pytest.fixture
def site(client) -> str:
    assert client.post("/api/scrive/sites", json={"id": "vids"}).status_code == 200
    _make_video(store.site_dir("vids") / "media" / "demo.mp4")
    page = store.site_dir("vids") / "posts" / "a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: Demo day\ndescription: A demo.\n---\n\nText.\n")
    return "vids"


def _wait(client: TestClient, job: dict, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/api/scrive/clip-jobs", params={"site": job["site"]}).json()
        current = next(j for j in jobs if j["id"] == job["id"])
        if current["status"] != "running":
            return current
        time.sleep(0.1)
    raise AssertionError("job did not finish")


@needs_ffmpeg
def test_clip_round_trip_render_and_draft(client, site):
    made = client.post(
        f"/api/scrive/sites/{site}/clips",
        json={"source": "media/demo.mp4", "page": "posts/a.md"},
    )
    assert made.status_code == 200, made.text
    doc = made.json()
    assert doc["path"] == "media/demo.clip.json"
    assert doc["probe"]["width"] == 320 and doc["probe"]["has_audio"] is True
    assert abs(doc["edit"]["segments"][0]["out"] - 3) < 0.1
    assert doc["rendered"] is False and doc["output"] == "media/demo.x.mp4"
    # A second clip of the same source gets its own file.
    again = client.post(
        f"/api/scrive/sites/{site}/clips", json={"source": "media/demo.mp4"}
    )
    assert again.json()["path"] == "media/demo-2.clip.json"

    listing = client.get(f"/api/scrive/sites/{site}/media").json()
    assert [c["path"] for c in listing["clips"]] == [
        "media/demo-2.clip.json",
        "media/demo.clip.json",
    ]
    assert [v["path"] for v in listing["videos"]] == ["media/demo.mp4"]

    cut = {**doc["edit"], "segments": [{"in": 0, "out": 1}, {"in": 2, "out": 2.5}]}
    cut["overlays"] = [{"t0": 0, "t1": 1, "text": "Demo", "pos": "top"}]
    saved = client.put(
        f"/api/scrive/sites/{site}/clip",
        params={"path": doc["path"]},
        json={"edit": cut, "base_revision": doc["revision"]},
    )
    assert saved.status_code == 200, saved.text
    stale = client.put(
        f"/api/scrive/sites/{site}/clip",
        params={"path": doc["path"]},
        json={"edit": doc["edit"], "base_revision": doc["revision"]},
    )
    assert stale.status_code == 409
    assert stale.json()["current"]["revision"] == saved.json()["revision"]

    # Drafting before a render exists is refused.
    early = client.post(
        f"/api/scrive/sites/{site}/clip/draft",
        json={"path": doc["path"], "target": "x"},
    )
    assert early.status_code == 400 and "render" in early.json()["detail"]

    job = client.post(
        f"/api/scrive/sites/{site}/clip/render", params={"path": doc["path"]}
    )
    assert job.status_code == 200, job.text
    done = _wait(client, job.json())
    assert done["status"] == "done", done["error"]
    out = store.site_dir(site) / "media" / "demo.x.mp4"
    rendered = clips.probe(out)
    assert abs(rendered.duration - 1.5) < 0.15
    assert (rendered.width, rendered.height) == (320, 180) and rendered.has_audio
    assert not list(out.parent.glob("*.scrive-tmp"))
    assert client.get(
        f"/api/scrive/sites/{site}/clip", params={"path": doc["path"]}
    ).json()["rendered"]

    draft = client.post(
        f"/api/scrive/sites/{site}/clip/draft",
        json={"path": doc["path"], "target": "x"},
    )
    assert draft.status_code == 200, draft.text
    item = draft.json()
    assert item["status"] == "draft" and item["page"] == "posts/a.md"
    assert item["payload"]["posts"][0]["media"] == ["media/demo.x.mp4"]
    assert item["payload"]["posts"][0]["text"].startswith("Demo day")
    yt = client.post(
        f"/api/scrive/sites/{site}/clip/draft",
        json={"path": doc["path"], "target": "youtube"},
    ).json()
    assert (
        yt["payload"]["video"] == "media/demo.x.mp4"
        and yt["payload"]["title"] == "Demo day"
    )


@needs_ffmpeg
def test_gif_render_and_filmstrip(client, site):
    doc = clips.create_clip(
        site,
        "media/demo.mp4",
        edit={
            "segments": [{"in": 0, "out": 1}],
            "crop": {"aspect": "1:1"},
            "output": {"preset": "gif", "gif_width": 120, "gif_fps": 8},
        },
    )
    job = client.post(
        f"/api/scrive/sites/{site}/clip/render", params={"path": doc.path}
    )
    done = _wait(client, job.json())
    assert done["status"] == "done", done["error"]
    gif = store.site_dir(site) / doc.output
    assert gif.read_bytes()[:6] == b"GIF89a"
    assert clips.probe(gif).width == 120

    strip = client.get(
        f"/api/scrive/sites/{site}/media/filmstrip",
        params={"path": "media/demo.mp4", "count": 6},
    )
    assert strip.status_code == 200 and strip.content[:2] == b"\xff\xd8"
    # Cached: the second ask is the same file.
    assert (
        len(
            list(
                (store.site_dir(site) / ".scrive" / "cache" / "filmstrip").glob("*.jpg")
            )
        )
        == 1
    )


@needs_ffmpeg
def test_auto_captions_follow_the_output_timeline(client, site, monkeypatch):
    heard: list[float] = []

    class FakeStt:
        async def transcribe(self, audio: bytes, language=None) -> str:
            import io
            import wave

            with wave.open(io.BytesIO(audio)) as w:
                heard.append(round(w.getnframes() / w.getframerate(), 1))
            return f"words {len(heard)}"

    fake = types.ModuleType("backend.modules.agent.stt_service")
    fake.stt_service = FakeStt()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.modules.agent.stt_service", fake)
    monkeypatch.setattr(
        extras,
        "probe",
        lambda name, **_: extras.Availability(extra=name, available=True, certain=True),
    )
    # The tone is silent from 1 s to 2 s of the source. At 1.5x the output is 2 s
    # long, with the silence at 0.67..1.33 of it.
    doc = clips.create_clip(
        site, "media/demo.mp4", edit={"segments": [{"in": 0, "out": 3}], "speed": 1.5}
    )
    job = client.post(
        f"/api/scrive/sites/{site}/clip/captions", params={"path": doc.path}
    )
    assert job.status_code == 200, job.text
    done = _wait(client, job.json())
    assert done["status"] == "done", done["error"]
    cues = done["cues"]
    assert [c["text"] for c in cues] == ["words 1", "words 2"]
    assert cues[0]["t0"] == 0 and abs(cues[0]["t1"] - 0.667) < 0.1
    assert abs(cues[1]["t0"] - 1.333) < 0.1 and abs(cues[1]["t1"] - 2.0) < 0.05


@needs_ffmpeg
def test_cancel_stops_a_render_and_leaves_nothing(client, site, monkeypatch):
    doc = clips.create_clip(site, "media/demo.mp4")
    started = media.jobs.start_render(site, doc.path)
    cancelled = media.jobs.cancel(started.id)
    assert cancelled.id == started.id
    deadline = time.time() + 30
    while media.jobs.get(started.id).status == "running" and time.time() < deadline:
        time.sleep(0.05)
    final = media.jobs.get(started.id)
    # A render that finished before the kill landed is fine; one that was cut off
    # is `cancelled` and wrote nothing.
    if final.status == "cancelled":
        assert not (store.site_dir(site) / doc.output).exists()
    else:
        assert final.status == "done"


@needs_ffmpeg
def test_a_browser_recording_is_remuxed_so_it_can_be_seeked(client, site):
    raw = store.site_dir(site) / "media" / "take.webm"
    with raw.open("wb") as handle:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=15:duration=2",
             "-c:v", "libvpx", "-b:v", "200k", "-f", "webm", "pipe:1"],
            stdout=handle, check=True, timeout=60,
        )  # fmt: skip
    # Written to a pipe, the header carries no duration, like MediaRecorder's.
    assert abs(clips.probe(raw).duration - 2) < 0.2  # measured, not read
    doc = clips.create_clip(site, "media/take.webm")
    assert abs(doc.edit["segments"][0]["out"] - 2) < 0.2
    assert clips.repair_header(raw) is False  # already repaired


def test_x_preflight_uses_the_clip_limits(client, site_for_preflight, monkeypatch):
    site_id, rel = site_for_preflight
    monkeypatch.setattr(social, "_duration", lambda _path: 150.0)
    findings = social.preflight(
        site_id, "", "x", {"posts": [{"text": "look", "media": [rel]}], "link": ""}
    )
    assert any(f.rule == "media" and "140" in f.message for f in findings)


@pytest.fixture
def site_for_preflight(client) -> tuple[str, str]:
    assert client.post("/api/scrive/sites", json={"id": "pf"}).status_code == 200
    path = store.site_dir("pf") / "media" / "long.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 64)
    return "pf", "media/long.mp4"


@needs_ffmpeg
def test_a_clip_with_no_page_drafts_without_a_page_link(client, site):
    doc = clips.create_clip(
        site,
        "media/demo.mp4",
        edit={
            "segments": [{"in": 0, "out": 0.5}],
            "output": {"preset": "gif", "gif_width": 120},
        },
    )
    done = _wait(
        client,
        client.post(
            f"/api/scrive/sites/{site}/clip/render", params={"path": doc.path}
        ).json(),
    )
    assert done["status"] == "done", done["error"]
    item = client.post(
        f"/api/scrive/sites/{site}/clip/draft", json={"path": doc.path, "target": "x"}
    ).json()
    assert item["page"] == "" and item["payload"]["link"] == ""
    assert item["payload"]["posts"][0]["media"] == [doc.output]
    # Nothing in it waits on a published page, so that rule stays quiet.
    findings = client.post(f"/api/scrive/outbox/{item['id']}/check").json()
    assert "unpublished" not in {f["rule"] for f in findings}
    # `page=` (empty) lists exactly the rows that belong to no page.
    on_page = client.post(
        "/api/scrive/outbox", json={"site": site, "page": "posts/a.md", "target": "x"}
    ).json()
    pageless = client.get(
        "/api/scrive/outbox", params={"site": site, "page": ""}
    ).json()
    assert [r["id"] for r in pageless] == [item["id"]]
    assert on_page["id"] not in [r["id"] for r in pageless]
    # A page-less draft started by hand has no link to fill either.
    blank = client.post(
        "/api/scrive/outbox", json={"site": site, "target": "linkedin"}
    ).json()
    assert blank["payload"]["link"] == ""


@needs_ffmpeg
def test_agent_make_clip_writes_an_edit_list_and_renders_it(client, site):
    import asyncio

    from backend.modules.scrive.agent_tools import make_clip

    result = asyncio.run(
        make_clip(
            {
                "site": site,
                "source": "media/demo.mp4",
                "name": "teaser",
                "page": "posts/a.md",
                "segments": [[0, 1], [2, 2.5]],
                "crop": {"aspect": "9:16"},
                "overlays": [{"t0": 0, "t1": 1, "text": "Demo day"}],
                "preset": "web",
            }
        )
    )
    assert result["clip"] == "media/teaser.clip.json"
    assert result["status"] == "done", result
    assert result["output"] == "media/teaser.web.mp4"
    assert result["embed_from_post"] == "```{video} ../media/teaser.web.mp4\n```"
    saved = json.loads((store.site_dir(site) / result["clip"]).read_text())
    assert saved["page"] == "posts/a.md"
    assert saved["segments"] == [{"in": 0.0, "out": 1.0}, {"in": 2.0, "out": 2.5}]
    rendered = clips.probe(store.site_dir(site) / result["output"])
    assert rendered.height > rendered.width  # 9:16
    assert abs(rendered.duration - 1.5) < 0.15

    refused = asyncio.run(
        make_clip({"site": site, "source": "media/demo.mp4", "segments": [[5, 6]]})
    )
    assert "Not rendered" in refused["note"] and "status" not in refused
    missing = asyncio.run(make_clip({"site": site, "source": "media/nope.mp4"}))
    assert "error" in missing
