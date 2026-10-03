"""Media jobs: rendering a clip, auto-captioning it, and the filmstrip a clip pane
draws its timeline over.

ffmpeg runs as a **blocking `Popen` pumped on a thread**, not
`asyncio.create_subprocess_exec`: under `uvicorn --reload` on Windows the loop is a
`SelectorEventLoop`, which cannot spawn subprocesses at all. The child gets its own
process group, so a console signal aimed at the backend does not stop it mid-file;
Cancel kills it by handle.

A render writes to a temporary name in a work folder and is moved over the output
only when ffmpeg exits cleanly, so a failed or cancelled render never leaves half a
video where a page expects a whole one.

Jobs live in memory: a backend restart forgets them (the files they finished are on
disk; one that was mid-render simply did not happen). Progress goes out on the
`scrive` WS channel as `clip.job` carrying the job.

Auto-captions transcribe the clip's sound **on the output timeline** with the local
Whisper (`agent/stt_service.py`, the `voice` extra): the audio is split at pauses
found by ffmpeg's `silencedetect`, long stretches are cut into pieces of at most
`MAX_CUE_S`, and each piece becomes one cue. The cues go back to the pane, which
merges them into its edit list — the job never writes the clip file, which the
person may be editing.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend import extras
from backend.atomic_write import replace_with_retry
from backend.modules.scrive import clips, store
from backend.modules.ws import broadcast_event

logger = logging.getLogger(__name__)

JobKind = Literal["render", "captions"]
JobStatus = Literal["running", "done", "failed", "cancelled"]

#: Longest caption cue; a longer stretch of speech is cut into even pieces.
MAX_CUE_S = 6.0
MIN_CUE_S = 0.3
SILENCE_DB = -35
SILENCE_S = 0.35
#: Progress events are throttled to this interval per job.
PROGRESS_EVERY_S = 0.25
#: Finished jobs kept for a pane that reopens.
KEEP_FINISHED = 40


class Job(BaseModel):
    id: str
    kind: JobKind
    site: str
    clip: str
    status: JobStatus = "running"
    progress: float = 0.0
    label: str = ""
    #: Render: the site path written.
    output: str = ""
    #: Captions: the cues found, on the output timeline.
    cues: list[clips.Cue] = Field(default_factory=list)
    findings: list[clips.ClipFinding] = Field(default_factory=list)
    error: str = ""
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None


class _Running:
    def __init__(self, job: Job, loop: asyncio.AbstractEventLoop | None) -> None:
        self.job = job
        self.loop = loop
        self.proc: subprocess.Popen[str] | None = None
        self.cancelled = False
        self.done = threading.Event()
        self.last_sent = 0.0


def _popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class MediaJobs:
    def __init__(self) -> None:
        self._jobs: dict[str, _Running] = {}
        self._lock = threading.Lock()

    # --- bookkeeping ------------------------------------------------------------------

    def list(self, site: str | None = None, clip: str | None = None) -> list[Job]:
        with self._lock:
            jobs = [r.job for r in self._jobs.values()]
        return sorted(
            (
                j
                for j in jobs
                if (site is None or j.site == site) and (clip is None or j.clip == clip)
            ),
            key=lambda j: j.started_at,
            reverse=True,
        )

    def get(self, job_id: str) -> Job:
        with self._lock:
            running = self._jobs.get(job_id)
        if running is None:
            raise FileNotFoundError(job_id)
        return running.job

    def _active(self, site: str, clip: str, kind: JobKind) -> Job | None:
        with self._lock:
            for r in self._jobs.values():
                j = r.job
                if (j.site, j.clip, j.kind, j.status) == (site, clip, kind, "running"):
                    return j
        return None

    def _forget_old(self) -> None:
        with self._lock:
            finished = sorted(
                (r for r in self._jobs.values() if r.job.status != "running"),
                key=lambda r: r.job.finished_at or 0,
            )
            for r in finished[: max(0, len(finished) - KEEP_FINISHED)]:
                self._jobs.pop(r.job.id, None)

    def _emit(self, running: _Running, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - running.last_sent < PROGRESS_EVERY_S:
            return
        running.last_sent = now
        loop = running.loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(
            broadcast_event("scrive", "clip.job", running.job.model_dump()), loop
        )

    def _finish(self, running: _Running, status: JobStatus, error: str = "") -> None:
        job = running.job
        job.status = "cancelled" if running.cancelled else status
        job.error = "" if job.status == "cancelled" else error
        if job.status == "done":
            job.progress = 1.0
        job.finished_at = time.time()
        running.proc = None
        self._emit(running, force=True)
        running.done.set()
        self._forget_old()

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            running = self._jobs.get(job_id)
        if running is None:
            raise FileNotFoundError(job_id)
        if running.job.status == "running":
            running.cancelled = True
            proc = running.proc
            if proc is not None and proc.poll() is None:
                proc.kill()
        return running.job

    async def wait(self, job_id: str, timeout: float) -> Job:
        with self._lock:
            running = self._jobs.get(job_id)
        if running is None:
            raise FileNotFoundError(job_id)
        await asyncio.to_thread(running.done.wait, timeout)
        return running.job

    def _start(
        self,
        job: Job,
        work: Callable[[_Running], None],
        loop: asyncio.AbstractEventLoop | None,
    ) -> Job:
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        running = _Running(job, loop)
        with self._lock:
            self._jobs[job.id] = running

        def body() -> None:
            try:
                work(running)
            except Exception as exc:  # noqa: BLE001 — every failure ends the job visibly
                logger.info("scrive %s job %s failed: %s", job.kind, job.id, exc)
                self._finish(running, "failed", str(exc) or exc.__class__.__name__)

        # Announced before the thread starts, so a quick job's `done` cannot reach
        # a pane ahead of its `running`.
        self._emit(running, force=True)
        threading.Thread(
            target=body, daemon=True, name=f"scrive-{job.kind}-{job.id}"
        ).start()
        return job

    # --- ffmpeg -----------------------------------------------------------------------

    def _run_ffmpeg(
        self, running: _Running, args: list[str], cwd: Path, duration: float,
        span: tuple[float, float] = (0.0, 1.0),
    ) -> None:  # fmt: skip
        """Run one ffmpeg command, turning its `-progress` lines into job progress
        within `span`. Raises with ffmpeg's own last words when it fails."""
        if running.cancelled:
            raise clips.ClipError("cancelled")
        err_path = cwd / "ffmpeg.log"
        with err_path.open("w", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen(
                args,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_popen_kwargs(),
            )
            running.proc = proc
            assert proc.stdout is not None
            lo, hi = span
            for line in proc.stdout:
                match = re.match(r"out_time_us=(\d+)", line)
                if match and duration > 0:
                    done = min(1.0, int(match.group(1)) / 1_000_000 / duration)
                    running.job.progress = round(lo + (hi - lo) * done, 4)
                    self._emit(running)
            code = proc.wait()
        if running.cancelled:
            raise clips.ClipError("cancelled")
        if code != 0:
            tail = err_path.read_text(encoding="utf-8", errors="replace").strip()
            last = [line for line in tail.splitlines() if line.strip()][-3:]
            raise clips.ClipError(
                "ffmpeg failed: " + (" / ".join(last) if last else f"exit {code}")
            )

    # --- render -----------------------------------------------------------------------

    def _load(
        self, site: str, clip_rel: str
    ) -> tuple[clips.EditList, clips.Probe, Path]:
        doc = clips.read_clip(site, clip_rel)
        edit = clips.parse(doc.edit)
        if doc.probe is None:
            raise clips.ClipError(f"{edit.source} is not a readable video")
        return edit, doc.probe, store.resolve_asset(site, edit.source)

    def start_render(
        self, site: str, clip_rel: str, loop: asyncio.AbstractEventLoop | None = None
    ) -> Job:
        existing = self._active(site, clip_rel, "render")
        if existing is not None:
            return existing
        clips.ffmpeg()
        edit, probe_, source = self._load(site, clip_rel)
        clips.validate(edit, probe_)
        replace = (
            store.resolve_asset(site, edit.audio.replace)
            if edit.audio.replace
            else None
        )
        out_rel = clips.output_path(clip_rel, edit)
        job = Job(
            id=secrets.token_hex(6), kind="render", site=site, clip=clip_rel,
            output=out_rel, label=f"rendering {clips.PRESETS[edit.output.preset].label}",
        )  # fmt: skip

        def work(running: _Running) -> None:
            ext = clips.PRESETS[edit.output.preset].ext
            plan = clips.compile_render(
                edit, probe_, source, f"out.{ext}", replace_audio=replace
            )
            with tempfile.TemporaryDirectory(prefix="scrive-render-") as tmp:
                work_dir = Path(tmp)
                if plan.ass is not None:
                    (work_dir / "captions.ass").write_text(plan.ass, encoding="utf-8")
                self._run_ffmpeg(running, plan.args, work_dir, plan.duration)
                produced = work_dir / f"out.{ext}"
                if not produced.is_file() or produced.stat().st_size == 0:
                    raise clips.ClipError("ffmpeg wrote nothing")
                target = store.site_dir(site) / out_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(f".{target.name}.scrive-tmp")
                shutil.copyfile(produced, staged)
                replace_with_retry(str(staged), target)
                size = target.stat().st_size
            running.job.findings = after_render(edit, size, plan.duration)
            running.job.label = (
                f"{plan.size[0]}x{plan.size[1]} · {size / clips.MB:.1f} MB"
            )
            self._finish(running, "done")

        return self._start(job, work, loop)

    # --- captions ---------------------------------------------------------------------

    def start_captions(
        self, site: str, clip_rel: str, loop: asyncio.AbstractEventLoop | None = None
    ) -> Job:
        existing = self._active(site, clip_rel, "captions")
        if existing is not None:
            return existing
        voice = extras.probe("voice")
        if not voice.available:
            raise clips.ClipError(
                "Auto-captions need local speech-to-text: "
                + (voice.install or voice.reason or "the voice extra")
            )
        clips.ffmpeg()
        edit, probe_, source = self._load(site, clip_rel)
        replace = (
            store.resolve_asset(site, edit.audio.replace)
            if edit.audio.replace
            else None
        )
        args = clips.compile_audio(
            edit, probe_, source, "audio.wav", replace_audio=replace
        )
        if args is None:
            raise clips.ClipError("The clip has no sound to caption.")
        job = Job(
            id=secrets.token_hex(6), kind="captions", site=site, clip=clip_rel,
            label="extracting the sound",
        )  # fmt: skip
        duration = clips.output_duration(edit)

        def work(running: _Running) -> None:
            from backend.modules.agent.stt_service import stt_service

            with tempfile.TemporaryDirectory(prefix="scrive-captions-") as tmp:
                work_dir = Path(tmp)
                self._run_ffmpeg(running, args, work_dir, duration, (0.0, 0.15))
                wav = work_dir / "audio.wav"
                running.job.label = "finding speech"
                self._emit(running, force=True)
                regions = speech_regions(silences(wav), duration)
                cues: list[clips.Cue] = []
                for i, (t0, t1) in enumerate(regions):
                    if running.cancelled:
                        raise clips.ClipError("cancelled")
                    running.job.label = f"transcribing {i + 1}/{len(regions)}"
                    running.job.progress = round(
                        0.2 + 0.8 * i / max(1, len(regions)), 4
                    )
                    self._emit(running, force=True)
                    text = transcribe_sync(
                        stt_service, wav_slice(wav, t0, t1), running.loop
                    )
                    if text:
                        cues.append(
                            clips.Cue(t0=round(t0, 2), t1=round(t1, 2), text=text)
                        )
            running.job.cues = cues
            running.job.label = f"{len(cues)} caption{'s' if len(cues) != 1 else ''}"
            self._finish(running, "done")

        return self._start(job, work, loop)


def transcribe_sync(
    service: Any, audio: bytes, loop: asyncio.AbstractEventLoop | None
) -> str:
    """The service's transcription, from this worker thread. Its public method is a
    coroutine that serializes Whisper passes with a lock, so it is run on the app's
    loop when there is one (queuing behind a voice-agent pass rather than racing it
    for VRAM), and in a loop of its own otherwise."""
    if loop is not None and not loop.is_closed():
        future = asyncio.run_coroutine_threadsafe(service.transcribe(audio), loop)
        return str(future.result()).strip()
    return str(asyncio.run(service.transcribe(audio))).strip()


def after_render(
    edit: clips.EditList, size: int, duration: float
) -> list[clips.ClipFinding]:
    """What the finished file means for where it can go."""
    preset = clips.PRESETS[edit.output.preset]
    findings: list[clips.ClipFinding] = []
    if preset.max_bytes and size > preset.max_bytes:
        limit = preset.max_bytes / clips.MB
        where = "X" if edit.output.preset in ("x", "gif") else preset.label
        findings.append(clips.ClipFinding(
            rule="size",
            message=f"{size / clips.MB:.1f} MB is over {where}'s {limit:.0f} MB limit"
            + (" — try a lower frame rate, a smaller width or a shorter clip." if edit.output.preset == "gif" else "."),
        ))  # fmt: skip
    if preset.max_seconds and duration > preset.max_seconds:
        findings.append(clips.ClipFinding(
            rule="duration",
            message=f"{preset.label} takes videos up to {int(preset.max_seconds)} seconds.",
        ))  # fmt: skip
    return findings


# --- speech regions -------------------------------------------------------------------

_SILENCE_START = re.compile(r"silence_start: (-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end: (-?[\d.]+)")


def silences(wav: Path) -> list[tuple[float, float]]:
    """Silent stretches in a WAV, by ffmpeg's `silencedetect`."""
    out = subprocess.run(
        [clips.ffmpeg(), "-hide_banner", "-nostdin", "-i", str(wav),
         "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_S}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )  # fmt: skip
    return parse_silences(out.stderr)


def parse_silences(log: str) -> list[tuple[float, float]]:
    found: list[tuple[float, float]] = []
    start: float | None = None
    for line in log.splitlines():
        if (m := _SILENCE_START.search(line)) is not None:
            start = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END.search(line)) is not None and start is not None:
            found.append((start, float(m.group(1))))
            start = None
    if start is not None:
        found.append((start, float("inf")))
    return found


def speech_regions(
    silent: list[tuple[float, float]], duration: float
) -> list[tuple[float, float]]:
    """The stretches between silences, each cut into pieces no longer than
    `MAX_CUE_S` and none shorter than `MIN_CUE_S`."""
    regions: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(silent):
        if start > cursor:
            regions.append((cursor, min(start, duration)))
        cursor = max(cursor, end)
    if cursor < duration:
        regions.append((cursor, duration))
    pieces: list[tuple[float, float]] = []
    for t0, t1 in regions:
        length = t1 - t0
        if length < MIN_CUE_S:
            continue
        count = max(1, int(-(-length // MAX_CUE_S)))
        step = length / count
        pieces += [(t0 + k * step, t0 + (k + 1) * step) for k in range(count)]
    return pieces


def wav_slice(wav: Path, t0: float, t1: float) -> bytes:
    """`t0..t1` of a PCM WAV, as a WAV of its own."""
    with wave.open(str(wav), "rb") as src:
        rate = src.getframerate()
        src.setpos(min(src.getnframes(), int(t0 * rate)))
        frames = src.readframes(max(0, int((t1 - t0) * rate)))
        params = src.getparams()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as dst:
        dst.setparams(params)
        dst.writeframes(frames)
    return buf.getvalue()


# --- the filmstrip --------------------------------------------------------------------

FILMSTRIP_HEIGHT = 72


def filmstrip(site: str, rel: str, count: int = 16) -> Path:
    """One JPEG of `count` frames side by side, evenly spaced over the video — the
    clip pane stretches it under its timeline. Cached by the file's size and mtime."""
    count = max(4, min(count, 48))
    source = store.resolve_asset(site, rel)
    stat = source.stat()
    key = hashlib.sha256(
        f"{rel}|{stat.st_size}|{stat.st_mtime_ns}|{count}".encode()
    ).hexdigest()[:20]
    cache = store.site_dir(site) / ".scrive" / "cache" / "filmstrip"
    target = cache / f"{key}.jpg"
    if target.is_file():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    probe_ = clips.probe(source)
    rate = count / max(probe_.duration, 0.1)
    staged = cache / f".{key}.tmp.jpg"
    out = subprocess.run(
        [clips.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source),
         "-vf", f"fps={rate:.6f},scale=-2:{FILMSTRIP_HEIGHT},tile={count}x1",
         "-frames:v", "1", "-q:v", "5", str(staged)],
        capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    if out.returncode != 0 or not staged.is_file():
        staged.unlink(missing_ok=True)
        raise clips.ClipError(
            f"could not draw the filmstrip: {out.stderr.strip()[:200]}"
        )
    replace_with_retry(str(staged), target)
    return target


jobs = MediaJobs()


# --- drafts from a render -------------------------------------------------------------


def draft_from_clip(
    site: str,
    clip_rel: str,
    target: str,
    *,
    created_by: Literal["person", "agent"] = "person",
):
    """An outbox **draft** carrying the clip's latest render: an X post with the
    video (or GIF) attached, or a YouTube upload of it. Starts from the page's
    frontmatter when the clip belongs to a page. Nothing is approved or sent."""
    from backend.modules.scrive import outbox, social

    if target not in ("x", "youtube"):
        raise clips.ClipError("a clip can be drafted for X or YouTube")
    doc = clips.read_clip(site, clip_rel)
    if not doc.rendered:
        raise clips.ClipError(f"render the clip first ({doc.output} is not there yet)")
    edit = clips.parse(doc.edit)
    page = edit.page
    if page:
        try:
            store.resolve_page(site, page)
        except (FileNotFoundError, store.StoreError):
            page = ""
    base = social.suggest(site, page, target) if page else social.blank(target)
    if target == "x":
        payload = social.XPayload.model_validate(base)
        first = payload.posts[0] if payload.posts else social.XPost()
        payload.posts = [
            first.model_copy(update={"media": [doc.output]}),
            *payload.posts[1:],
        ]
    else:
        payload = social.YouTubePayload.model_validate(base)
        payload.title = payload.title or clips.clip_stem(clip_rel)
        payload.video = doc.output
    return outbox.create(
        site, page, target, payload.model_dump(), created_by=created_by
    )
