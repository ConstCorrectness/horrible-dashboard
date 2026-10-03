"""Clip edit lists, and what ffmpeg is asked to do with them.

A clip is one source video and a JSON **edit list** beside it
(`media/<name>.clip.json`): which stretches of the source to keep and in what order,
a crop, a speed, captions and text overlays, and what to do with the audio. Nothing
here touches the source: a render writes a new file (`media/<name>.<preset>.mp4`, or
`.gif`), and re-rendering the same clip replaces that file, because a page that
embeds it wants the new cut.

Times: `segments` are in **source** seconds; `captions` and `overlays` are in
**output** seconds (after the segments are joined and the speed applied), which is
the timeline the person watches and captions against.

`compile_render` turns an edit list into one ffmpeg command — trim/atrim and concat,
then crop and scale, then captions burned in from a generated `.ass` file — and is
pure, so the tests pin the exact filtergraph. A GIF is the two-pass palette
(`palettegen` then `paletteuse`) inside that one graph.

`PRESETS` is the per-target limits table. Rendering fits the output inside it, and
the outbox's preflight (`social.py`) checks X media against the same numbers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.atomic_write import replace_with_retry
from backend.modules.scrive import store

CLIP_SUFFIX = ".clip.json"
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
#: Folders a media listing never walks.
_SKIP = store.SKIP_DIRS | {".scrive", ".git", "_build"}


class ClipError(ValueError):
    pass


# --- the limits table -----------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    """What a render for one destination may be."""

    label: str
    ext: str
    #: The box the frame is fitted inside, as (long side, short side). Never upscaled.
    box: tuple[int, int]
    max_fps: int
    #: The destination refuses longer (None: no limit worth checking here).
    max_seconds: float | None
    max_bytes: int | None
    crf: int = 23


MB = 1024 * 1024

PRESETS: dict[str, Preset] = {
    # X: up to 1920x1200 (or 1200x1900), 60 fps, 2:20, 512 MB.
    "x": Preset("X", "mp4", (1920, 1200), 60, 140.0, 512 * MB),
    # YouTube takes far more; 1080p H.264 is what a dev log needs. A vertical crop
    # under three minutes becomes a Short on YouTube's side.
    "youtube": Preset("YouTube", "mp4", (1920, 1080), 60, None, None, crf=20),
    # For embedding in a page: small and quick to load.
    "web": Preset("Web", "mp4", (1280, 720), 30, None, None, crf=26),
    # X takes an animated GIF up to 15 MB; most places choke well before that.
    "gif": Preset("GIF", "gif", (480, 480), 15, None, 15 * MB),
}

X_IMAGE_BYTES = 5 * MB
X_GIF_BYTES = PRESETS["gif"].max_bytes or 15 * MB
X_VIDEO_BYTES = PRESETS["x"].max_bytes or 512 * MB
X_VIDEO_SECONDS = PRESETS["x"].max_seconds or 140.0

ASPECTS: dict[str, tuple[int, int] | None] = {
    "source": None,
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
}


# --- the edit list --------------------------------------------------------------------


class Segment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start: float = Field(alias="in", ge=0)
    end: float = Field(alias="out", ge=0)


class Crop(BaseModel):
    aspect: Literal["source", "16:9", "9:16", "1:1", "4:5"] = "source"
    #: Centre of the crop window, as a fraction of the frame (0..1).
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


class Cue(BaseModel):
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    text: str = ""


class Overlay(Cue):
    pos: Literal["top", "center", "bottom"] = "top"


class Audio(BaseModel):
    #: A site path; replaces the source's sound.
    replace: str = ""
    gain: float = Field(1.0, ge=0, le=4)
    mute: bool = False


class Output(BaseModel):
    preset: Literal["x", "youtube", "web", "gif"] = "x"
    gif_fps: int = Field(12, ge=4, le=30)
    gif_width: int = Field(480, ge=120, le=1080)
    #: Burn the captions into the picture (the only way X and a GIF show them).
    burn_captions: bool = True


class EditList(BaseModel):
    version: int = 1
    source: str
    #: The page this clip belongs to, if any: drafts made from it link there.
    page: str = ""
    segments: list[Segment] = Field(default_factory=list)
    crop: Crop = Field(default_factory=Crop)
    speed: float = Field(1.0, ge=0.25, le=4)
    captions: list[Cue] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    audio: Audio = Field(default_factory=Audio)
    output: Output = Field(default_factory=Output)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        value = value.strip().lstrip("/")
        if not value:
            raise ValueError("an edit list needs a source")
        return value

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


def output_duration(edit: EditList) -> float:
    return sum(max(0.0, s.end - s.start) for s in edit.segments) / edit.speed


# --- probing --------------------------------------------------------------------------


class Probe(BaseModel):
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise ClipError("ffprobe is not on PATH — install ffmpeg")
    return path


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise ClipError("ffmpeg is not on PATH — install ffmpeg")
    return path


def _rate(text: str) -> float:
    num, _, den = (text or "0/1").partition("/")
    try:
        return float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        return 0.0


def _measured_duration(path: Path) -> float:
    """The length of a file whose header does not say (a browser's MediaRecorder
    WebM): remux it to nowhere and read how far it got. `-c copy` makes it quick."""
    out = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(path), "-map", "0", "-c", "copy",
         "-f", "null", "-", "-progress", "pipe:1", "-nostats"],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    times = [int(m) for m in re.findall(r"out_time_us=(\d+)", out.stdout)]
    return max(times, default=0) / 1_000_000


def probe(path: Path) -> Probe:
    out = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries",
         "stream=codec_type,width,height,avg_frame_rate,r_frame_rate:"
         "stream_side_data=rotation:stream_tags=rotate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    if out.returncode != 0:
        raise ClipError(
            f"ffprobe could not read {path.name}: {out.stderr.strip()[:200]}"
        )
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ClipError(f"{path.name} has no video stream")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    rotation = rotation or int((video.get("tags") or {}).get("rotate") or 0)
    if abs(rotation) % 180 == 90:
        # ffmpeg turns the picture upright when decoding, so crop in that frame.
        width, height = height, width
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        duration = _measured_duration(path)
    fps = _rate(video.get("avg_frame_rate", "")) or _rate(video.get("r_frame_rate", ""))
    return Probe(
        duration=round(duration, 3),
        width=width,
        height=height,
        # A WebM from a browser reports 1000 fps as its time base; treat as unknown.
        fps=round(fps, 3) if 0 < fps <= 240 else 30.0,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def repair_header(path: Path) -> bool:
    """Remux a file whose header lacks its duration (a browser recording) so players
    can seek in it. Lossless (`-c copy`); answers whether it rewrote the file."""
    out = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    try:
        if float(out.stdout.strip()) > 0:
            return False
    except ValueError:
        pass
    tmp = path.with_name(f".{path.stem}.remux{path.suffix}")
    done = subprocess.run(
        [ffmpeg(), "-v", "error", "-y", "-i", str(path), "-map", "0", "-c", "copy", str(tmp)],
        capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    if done.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False
    replace_with_retry(str(tmp), path)
    return True


# --- geometry -------------------------------------------------------------------------


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def crop_rect(crop: Crop, width: int, height: int) -> tuple[int, int, int, int]:
    """`(w, h, x, y)` of the crop window: the largest rectangle of the aspect that fits
    the frame, centred on the focus point and kept inside the frame."""
    ratio = ASPECTS[crop.aspect]
    if ratio is None:
        return _even(width), _even(height), 0, 0
    aspect = ratio[0] / ratio[1]
    if width / height > aspect:
        h = _even(height)
        w = _even(height * aspect)
    else:
        w = _even(width)
        h = _even(width / aspect)
    x = min(max(0, round(crop.x * width - w / 2)), width - w)
    y = min(max(0, round(crop.y * height - h / 2)), height - h)
    return w, h, x // 2 * 2, y // 2 * 2


def output_size(edit: EditList, probe_: Probe) -> tuple[int, int]:
    """The rendered frame: the crop fitted inside the preset's box, never enlarged."""
    w, h, _x, _y = crop_rect(edit.crop, probe_.width, probe_.height)
    preset = PRESETS[edit.output.preset]
    if edit.output.preset == "gif":
        long_side = short_side = edit.output.gif_width
    else:
        long_side, short_side = preset.box
    scale = min(1.0, long_side / max(w, h), short_side / min(w, h))
    return _even(w * scale), _even(h * scale)


# --- captions as ASS ------------------------------------------------------------------

_ALIGN = {"bottom": 2, "center": 5, "top": 8}


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    """ASS reads `{…}` as override tags and `\\N`-style escapes; the person's words
    should print as typed, so braces become parentheses and a backslash a lookalike."""
    text = text.replace("\\", "∖").replace("{", "(").replace("}", ")")
    return "\\N".join(line.strip() for line in text.strip().splitlines())


def build_ass(edit: EditList, width: int, height: int, duration: float) -> str:
    """Captions (bottom, white on a dark outline) and overlays (bold, at their
    position), sized for the output frame. Cues past the end are dropped."""
    caption_size = max(14, round(height * 0.055))
    overlay_size = max(16, round(height * 0.07))
    margin = max(8, round(height * 0.05))
    outline = max(1, round(height / 360))
    fmt = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding"
    )

    def style(name: str, size: int, bold: bool, align: int) -> str:
        return (
            f"Style: {name},Arial,{size},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
            f"{-1 if bold else 0},0,0,0,100,100,0,0,1,{outline},0,{align},"
            f"{margin},{margin},{margin},1"
        )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        fmt,
        style("Caption", caption_size, False, 2),
        *(style(pos.title(), overlay_size, True, a) for pos, a in _ALIGN.items()),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    cues: list[tuple[int, Cue, str]] = []
    if edit.output.burn_captions:
        cues += [(0, c, "Caption") for c in edit.captions]
    cues += [(1, o, o.pos.title()) for o in edit.overlays]
    for layer, cue, name in cues:
        t1 = min(cue.t1, duration)
        if cue.t0 >= t1 or not cue.text.strip():
            continue
        lines.append(
            f"Dialogue: {layer},{_ass_time(cue.t0)},{_ass_time(t1)},{name},,0,0,0,,"
            f"{_ass_text(cue.text)}"
        )
    return "\n".join(lines) + "\n"


def has_burned_text(edit: EditList) -> bool:
    return any(o.text.strip() for o in edit.overlays) or (
        edit.output.burn_captions and any(c.text.strip() for c in edit.captions)
    )


# --- the ffmpeg command ---------------------------------------------------------------


def _atempo(speed: float) -> list[str]:
    """atempo takes 0.5–2.0 per instance; chain for anything beyond."""
    parts: list[str] = []
    while speed > 2.0:
        parts.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        parts.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1.0) > 1e-6:
        parts.append(f"atempo={speed:.6g}")
    return parts


def _num(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


@dataclass(frozen=True)
class RenderPlan:
    args: list[str]
    #: Contents of `captions.ass`, written beside the output before ffmpeg runs.
    ass: str | None
    duration: float
    size: tuple[int, int]


def _audio_graph(
    edit: EditList, probe_: Probe, duration: float, *, for_output: bool
) -> tuple[list[str], str | None, list[str]]:
    """`(filter chains, output label, extra inputs)` for the clip's sound. With
    `for_output` False (captioning) a mute is ignored: the words are still there."""
    if edit.audio.replace:
        chain = [f"[1:a]atrim=end={_num(duration)},asetpts=PTS-STARTPTS"]
        if for_output and abs(edit.audio.gain - 1) > 1e-6:
            chain.append(f"volume={_num(edit.audio.gain)}")
        return [",".join(chain) + "[aout]"], "[aout]", [edit.audio.replace]
    if not probe_.has_audio or (for_output and edit.audio.mute):
        return [], None, []
    chains = [
        f"[0:a]atrim=start={_num(s.start)}:end={_num(s.end)},asetpts=PTS-STARTPTS[a{i}]"
        for i, s in enumerate(edit.segments)
    ]
    labels = "".join(f"[a{i}]" for i in range(len(edit.segments)))
    tail = [f"{labels}concat=n={len(edit.segments)}:v=0:a=1", *_atempo(edit.speed)]
    if for_output and abs(edit.audio.gain - 1) > 1e-6:
        tail.append(f"volume={_num(edit.audio.gain)}")
    chains.append(",".join(tail) + "[aout]")
    return chains, "[aout]", []


def validate(edit: EditList, probe_: Probe) -> None:
    """What makes an edit list unrenderable (as opposed to merely ill-advised —
    see `check`)."""
    if not edit.segments:
        raise ClipError("the clip has no segments")
    for i, seg in enumerate(edit.segments, 1):
        if seg.end - seg.start < 0.05:
            raise ClipError(f"segment {i} is empty (out must be after in)")
        if seg.start >= probe_.duration + 0.05:
            raise ClipError(f"segment {i} starts after the source ends")


def compile_render(
    edit: EditList,
    probe_: Probe,
    source: Path,
    output: str,
    *,
    replace_audio: Path | None = None,
) -> RenderPlan:
    """The ffmpeg argv for rendering `edit` to `output` (a file name; ffmpeg runs in
    a work folder holding `captions.ass`, so no path in the graph needs escaping)."""
    validate(edit, probe_)
    preset = PRESETS[edit.output.preset]
    duration = output_duration(edit)
    width, height = output_size(edit, probe_)
    cw, ch, cx, cy = crop_rect(edit.crop, probe_.width, probe_.height)

    chains = [
        f"[0:v]trim=start={_num(s.start)}:end={_num(s.end)},setpts=PTS-STARTPTS[v{i}]"
        for i, s in enumerate(edit.segments)
    ]
    labels = "".join(f"[v{i}]" for i in range(len(edit.segments)))
    video = [f"{labels}concat=n={len(edit.segments)}:v=1:a=0"]
    if abs(edit.speed - 1.0) > 1e-6:
        video.append(f"setpts=PTS/{_num(edit.speed)}")
    if (cw, ch) != (_even(probe_.width), _even(probe_.height)) or cx or cy:
        video.append(f"crop={cw}:{ch}:{cx}:{cy}")
    video.append(f"scale={width}:{height}:flags=lanczos,setsar=1")
    ass = build_ass(edit, width, height, duration) if has_burned_text(edit) else None
    if ass is not None:
        video.append("subtitles=captions.ass")

    is_gif = edit.output.preset == "gif"
    if is_gif:
        video.append(f"fps={edit.output.gif_fps},split[s0][s1]")
        chains.append(",".join(video))
        chains.append("[s0]palettegen=stats_mode=diff[pal]")
        chains.append(
            "[s1][pal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[vout]"
        )
        audio_chains, audio_label, extra = [], None, []
    else:
        if probe_.fps > preset.max_fps:
            video.append(f"fps={preset.max_fps}")
        chains.append(",".join(video) + "[vout]")
        audio_chains, audio_label, extra = _audio_graph(
            edit, probe_, duration, for_output=True
        )
        chains += audio_chains

    args = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(source)]
    if extra:
        args += ["-i", str(replace_audio or extra[0])]
    args += ["-filter_complex", ";".join(chains), "-map", "[vout]"]
    if is_gif:
        args += ["-loop", "0"]
    else:
        args += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(preset.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        ]  # fmt: skip
        if audio_label:
            args += ["-map", audio_label, "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
        else:
            args += ["-an"]
    args += ["-t", _num(duration), "-progress", "pipe:1", "-nostats", output]
    return RenderPlan(args=args, ass=ass, duration=duration, size=(width, height))


def compile_audio(edit: EditList, probe_: Probe, source: Path, output: str,
                  *, replace_audio: Path | None = None) -> list[str] | None:  # fmt: skip
    """The argv that writes the clip's sound, on the output timeline, as 16 kHz mono
    WAV — what auto-captions transcribe. None when the clip has no sound."""
    validate(edit, probe_)
    duration = output_duration(edit)
    chains, label, extra = _audio_graph(edit, probe_, duration, for_output=False)
    if label is None:
        return None
    args = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(source)]
    if extra:
        args += ["-i", str(replace_audio or extra[0])]
    return args + [
        "-filter_complex", ";".join(chains), "-map", label,
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-progress", "pipe:1", "-nostats", output,
    ]  # fmt: skip


# --- findings -------------------------------------------------------------------------


class ClipFinding(BaseModel):
    severity: Literal["info", "warning", "error"] = "warning"
    rule: str
    message: str


def _clock(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def check(edit: EditList, probe_: Probe | None) -> list[ClipFinding]:
    """What the clip pane shows beside Render: errors stop a render; warnings say
    where the result will not go."""
    findings: list[ClipFinding] = []
    if probe_ is None:
        return [
            ClipFinding(
                severity="error",
                rule="source",
                message=f"{edit.source} is not a readable video.",
            )
        ]
    try:
        validate(edit, probe_)
    except ClipError as exc:
        findings.append(
            ClipFinding(severity="error", rule="segments", message=str(exc))
        )
        return findings
    duration = output_duration(edit)
    for i, seg in enumerate(edit.segments, 1):
        if seg.end > probe_.duration + 0.05:
            findings.append(ClipFinding(
                rule="segments",
                message=f"Segment {i} runs past the end of the source ({_clock(probe_.duration)}); it stops there.",
            ))  # fmt: skip
    preset = PRESETS[edit.output.preset]
    if preset.max_seconds and duration > preset.max_seconds:
        findings.append(ClipFinding(
            rule="duration",
            message=f"{preset.label} takes videos up to {_clock(preset.max_seconds)}; this clip is {_clock(duration)}.",
        ))  # fmt: skip
    if edit.output.preset == "gif" and duration > 20:
        findings.append(ClipFinding(
            rule="duration",
            message=f"A {_clock(duration)} GIF will be large; GIFs work best under 10 seconds.",
        ))  # fmt: skip
    if edit.output.preset == "youtube" and duration < 1:
        findings.append(
            ClipFinding(
                rule="duration", message="YouTube refuses a video under a second."
            )
        )
    for kind, cues in (("caption", edit.captions), ("overlay", edit.overlays)):
        late = [c for c in cues if c.t0 >= duration]
        if late:
            findings.append(ClipFinding(
                rule=kind,
                message=f"{len(late)} {kind}{'s' if len(late) > 1 else ''} start after the clip ends ({_clock(duration)}) and won't show.",
            ))  # fmt: skip
    if edit.captions and not edit.output.burn_captions:
        findings.append(ClipFinding(
            severity="info", rule="caption",
            message="Captions are not burned in, so the render carries none; the page's player and YouTube can use a caption file instead.",
        ))  # fmt: skip
    if edit.output.preset == "gif" and (edit.audio.replace or probe_.has_audio):
        findings.append(
            ClipFinding(severity="info", rule="audio", message="A GIF has no sound.")
        )
    return findings


# --- files ----------------------------------------------------------------------------


def _guard(site_id: str, rel: str) -> Path:
    base = store.site_dir(site_id).resolve()
    resolved = (base / rel.strip().lstrip("/")).resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise store.StoreError(f"path escapes the site: {rel}")
    if resolved.relative_to(base).parts[0] in {".git", ".scrive"}:
        raise store.StoreError(f"not a clip: {rel}")
    if not resolved.name.endswith(CLIP_SUFFIX):
        raise store.StoreError(f"a clip file ends in {CLIP_SUFFIX}: {rel}")
    return resolved


def clip_stem(rel: str) -> str:
    name = PurePosixPath(rel).name
    return (
        name[: -len(CLIP_SUFFIX)]
        if name.endswith(CLIP_SUFFIX)
        else PurePosixPath(rel).stem
    )


def output_path(clip_rel: str, edit: EditList) -> str:
    """Where a render of this clip lands: beside the clip, named for it and the
    preset (`demo.x.mp4`, `demo.gif`). Never the source itself."""
    parent = PurePosixPath(clip_rel).parent
    preset = PRESETS[edit.output.preset]
    stem = clip_stem(clip_rel)
    name = (
        f"{stem}.gif"
        if preset.ext == "gif"
        else f"{stem}.{edit.output.preset}.{preset.ext}"
    )
    rel = str(parent / name) if str(parent) != "." else name
    if rel == edit.source:
        rel = rel.replace(f".{preset.ext}", f"-edit.{preset.ext}")
    return rel


class ClipDoc(BaseModel):
    path: str
    revision: str
    edit: dict[str, Any]
    probe: Probe | None = None
    findings: list[ClipFinding] = Field(default_factory=list)
    #: Where a render with the current preset lands, and whether one is there.
    output: str = ""
    rendered: bool = False


class ClipConflict(Exception):
    def __init__(self, current: ClipDoc) -> None:
        super().__init__("stale revision")
        self.current = current


def parse(data: bytes | str | dict[str, Any]) -> EditList:
    try:
        raw = json.loads(data) if isinstance(data, (bytes, str)) else data
        return EditList.model_validate(raw)
    except (ValueError, TypeError) as exc:
        raise store.StoreError(f"not a valid edit list: {exc}") from exc


def _encode(edit: EditList) -> bytes:
    return (json.dumps(edit.dump(), indent=2) + "\n").encode("utf-8")


def source_probe(site_id: str, edit: EditList) -> Probe | None:
    try:
        return probe(store.resolve_asset(site_id, edit.source))
    except (FileNotFoundError, store.StoreError, ClipError, subprocess.SubprocessError):
        return None


def _doc(site_id: str, rel: str, data: bytes) -> ClipDoc:
    edit = parse(data)
    probe_ = source_probe(site_id, edit)
    out = output_path(rel, edit)
    try:
        rendered = store.resolve_asset(site_id, out).is_file()
    except (FileNotFoundError, store.StoreError):
        rendered = False
    return ClipDoc(
        path=rel,
        revision=store.revision_of(data),
        edit=edit.dump(),
        probe=probe_,
        findings=check(edit, probe_),
        output=out,
        rendered=rendered,
    )


def read_clip(site_id: str, rel: str) -> ClipDoc:
    path = _guard(site_id, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    return _doc(site_id, rel, path.read_bytes())


def save_clip(
    site_id: str, rel: str, edit: dict[str, Any], base_revision: str
) -> ClipDoc:
    path = _guard(site_id, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    current = path.read_bytes()
    if base_revision and store.revision_of(current) != base_revision:
        raise ClipConflict(_doc(site_id, rel, current))
    data = _encode(parse(edit))
    store.write_bytes_atomic(path, data)
    return _doc(site_id, rel, data)


def create_clip(
    site_id: str,
    source: str,
    *,
    name: str = "",
    page: str = "",
    edit: dict[str, Any] | None = None,
) -> ClipDoc:
    """A new edit list for `source`, beside it: the whole source as one segment
    unless `edit` says otherwise. A recording whose header lacks its duration is
    remuxed first (losslessly), so it can be seeked."""
    source = source.strip().lstrip("/")
    src = store.resolve_asset(site_id, source)
    if src.suffix.lower() not in VIDEO_SUFFIXES:
        raise store.StoreError(f"not a video: {source}")
    if src.suffix.lower() == ".webm":
        repair_header(src)
    probe_ = probe(src)
    if page:
        store.resolve_page(site_id, page)
    base = parse({"source": source, "page": page, **(edit or {})})
    base.source = source
    if page:
        base.page = page
    if not base.segments:
        base.segments = [Segment(start=0, end=probe_.duration)]
    parent = PurePosixPath(source).parent
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name or PurePosixPath(source).stem).strip(
        ".-"
    )
    stem = stem.removesuffix(".clip") or "clip"
    folder = store.site_dir(site_id) / parent
    candidate, n = f"{stem}{CLIP_SUFFIX}", 2
    while (folder / candidate).exists():
        candidate = f"{stem}-{n}{CLIP_SUFFIX}"
        n += 1
    rel = str(parent / candidate) if str(parent) != "." else candidate
    path = _guard(site_id, rel)
    data = _encode(base)
    # Exclusive create: two "new clip" clicks must not write the same file.
    with open(path, "xb") as handle:
        handle.write(data)
    return _doc(site_id, rel, data)


class MediaFile(BaseModel):
    path: str
    size: int
    updated_at: float


class MediaListing(BaseModel):
    clips: list[MediaFile]
    videos: list[MediaFile]
    audio: list[MediaFile]


def list_media(site_id: str) -> MediaListing:
    base = store.site_dir(site_id)
    clips: list[MediaFile] = []
    videos: list[MediaFile] = []
    audio: list[MediaFile] = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP and not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(folder) / name
            stat = path.stat()
            item = MediaFile(
                path=path.relative_to(base).as_posix(),
                size=stat.st_size,
                updated_at=stat.st_mtime,
            )
            suffix = path.suffix.lower()
            if name.endswith(CLIP_SUFFIX):
                clips.append(item)
            elif suffix in VIDEO_SUFFIXES:
                videos.append(item)
            elif suffix in AUDIO_SUFFIXES:
                audio.append(item)
    return MediaListing(clips=clips, videos=videos, audio=audio)
