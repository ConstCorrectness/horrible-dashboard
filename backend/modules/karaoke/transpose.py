"""Pitch shift: re-encode a song's audio N semitones up or down, on the fly.

Shifting pitch *without* changing tempo is three ffmpeg filters, not one:
`asetrate` resamples the stream so it plays faster and higher (the tape-speed
trick), `aresample` puts the sample rate back where the container expects it, and
`atempo` slows it back to the original speed — leaving only the pitch change.
Dropping the `atempo` stage is the classic mistake and yields chipmunks.

`atempo` accepts 0.5–2.0 per instance. ±6 semitones needs at most 2^(6/12) ≈ 1.414,
comfortably inside one stage, which is why the model clamps there rather than
chaining filters.

**Why this is a streaming transcode and not a cached file.** A cached shifted copy
would be seekable and cheaper on a second play, but singers change key *while
looking for their key* — up two, no, down one — and each change would be a
full-length re-encode before a single note came out. A live pipe starts playing in
about a second at the cost of losing seek, which is the right trade for the one
control people actually reach for mid-song.

Spawning goes through `subprocess.Popen` on a worker thread rather than
`asyncio.create_subprocess_exec`. Under `uvicorn --reload` on Windows the loop is a
`SelectorEventLoop`, which cannot spawn subprocesses at all — the same trap the LSP
and PTY managers hit.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger(__name__)

# How much to read off the pipe at a time. Small enough that playback starts
# promptly, large enough not to context-switch per frame.
_CHUNK = 64 * 1024


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def available() -> bool:
    return ffmpeg_path() is not None


def _filter_chain(semitones: int) -> str:
    ratio = 2 ** (semitones / 12)
    return f"asetrate=44100*{ratio:.6f},aresample=44100,atempo={1 / ratio:.6f}"


def build_command(source: Path, semitones: int) -> list[str]:
    """The ffmpeg invocation. Split out so a test can assert the filter chain
    without spawning anything."""
    return [
        ffmpeg_path() or "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-af",
        _filter_chain(semitones),
        # Copy the video through untouched — re-encoding it would cost real CPU
        # for a change nobody asked for, and the picture is a lyric slideshow.
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # Fragmented MP4: a normal MP4's index sits at the end of the file, which
        # a pipe can't seek back to write. Without these flags ffmpeg fails
        # outright on a non-seekable output.
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        "pipe:1",
    ]


async def stream(source: Path, semitones: int) -> AsyncIterator[bytes]:
    """Yield a pitch-shifted MP4 for `source`, transcoding as it goes."""
    command = build_command(source, semitones)
    process = await asyncio.to_thread(
        subprocess.Popen,
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        while True:
            chunk = await asyncio.to_thread(process.stdout.read, _CHUNK)
            if not chunk:
                break
            yield chunk
    except asyncio.CancelledError:
        # The listener changed key or skipped the song. Killing the process here
        # is not tidiness: without it every abandoned transcode keeps running to
        # the end of a four-minute song, and a few key changes saturate the CPU.
        raise
    finally:
        if process.poll() is None:
            process.kill()
        # Drain so the pipe buffers can't wedge the child, and surface why a
        # transcode produced nothing.
        try:
            _, err = await asyncio.to_thread(process.communicate)
        except Exception:
            err = b""
        if process.returncode not in (0, None) and err:
            logger.warning(
                "ffmpeg transpose exited %s: %s",
                process.returncode,
                err.decode("utf-8", "replace")[:500],
            )
