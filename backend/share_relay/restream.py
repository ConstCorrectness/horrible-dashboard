"""Pushing a live room out to RTMP (Twitch, YouTube Live, anything) via ffmpeg.

## Why a subprocess and not PyAV in-process

PyAV is already here — aiortc depends on it — so encoding H.264 inside the relay
process would need no new dependency at all. It is still the wrong shape:

- **Encoding is the expensive thing, and this process is latency-critical.** The
  relay already pays one encode *per viewer* (see `fanout.py`); adding a 1080p
  H.264 encode on the same event loop makes every viewer's stream stutter to
  serve a broadcast nobody in the room can see.
- **A transcode is the thing most likely to crash or wedge**, and in a subprocess
  that is an exit code. In-process it is a dead relay and 25 dropped viewers.

The frames are already decoded by the time they get here (aiortc decodes on the
receive side), so feeding ffmpeg raw video costs the relay nothing beyond the
pipe write — the expensive half happens on the other side of it.

## What is fragile here, and why it is written this way

**ffmpeg must be told the input format exactly.** Raw video on a pipe has no
header: no dimensions, no pixel format, no rate. Get any of them wrong and
ffmpeg does not fail — it produces a picture that is skewed, wrongly coloured, or
running at the wrong speed, which reads as "the encoder is broken" rather than
"the arguments were wrong". So the first frame decides the geometry and every
later frame is reformatted to match it.

**A resolution change mid-stream is a real event.** A host who shares a window
and then resizes it changes the frame size, and RTMP cannot renegotiate. Frames
are therefore scaled to the size the stream started at rather than restarting the
encoder, because a broadcast that drops for four seconds every time somebody
drags a window edge is worse than one that is very slightly soft.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

logger = logging.getLogger(__name__)

#: Output frame rate. Matched to the capture's own 15fps (see `share/capture.ts`)
#: rather than the 30 broadcasters default to: sending 30 when the source makes
#: 15 doubles the encode cost to duplicate every frame.
FPS = 15

#: How long to wait for ffmpeg to exit on its own before killing it. It flushes
#: and closes the RTMP connection on stdin EOF, and a broadcast that is cut off
#: mid-GOP shows as a corrupt tail on the recording the platform keeps.
SHUTDOWN_TIMEOUT_S = 5.0


def ffmpeg_available() -> bool:
    """Whether ffmpeg is on PATH. Optional, exactly like karaoke's transposition."""
    return shutil.which("ffmpeg") is not None


def build_args(target: str, width: int, height: int) -> list[str]:
    """The ffmpeg command line. Pure, so the arguments can be tested without
    spawning anything — the failure mode here is silent corruption, not a crash.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        # -- input: raw frames on stdin, described exactly ----------------------
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        "-s",
        f"{width}x{height}",
        "-r",
        str(FPS),
        # Timestamps are generated from the input rate rather than taken from the
        # source: WebRTC frame timestamps have gaps whenever the encoder drops a
        # frame, and RTMP wants a monotonic stream.
        "-use_wallclock_as_timestamps",
        "0",
        "-i",
        "pipe:0",
        # -- output ------------------------------------------------------------
        "-c:v",
        "libx264",
        # `zerolatency` matters more than the preset: without it x264 buffers
        # frames looking ahead, and the broadcast lags the room by seconds.
        "-tune",
        "zerolatency",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        # Every platform requires a keyframe at least every 2s; without this a
        # viewer joining mid-stream waits until the next natural one, which on a
        # static screen share can be a very long time.
        "-g",
        str(FPS * 2),
        "-keyint_min",
        str(FPS * 2),
        "-b:v",
        "2500k",
        "-maxrate",
        "2500k",
        "-bufsize",
        "5000k",
        "-f",
        "flv",
        target,
    ]


class Restream:
    """One RTMP push, fed from one room's video track."""

    def __init__(self, token: str, target: str, label: str) -> None:
        self.token = token
        #: Carries the stream key. Never logged -- see `streaming.redact`.
        self._target = target
        #: A safe name for the UI and the logs.
        self.label = label
        self.error: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self._size: tuple[int, int] | None = None

    @property
    def live(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, track: Any) -> None:
        """Spawn ffmpeg and start pumping `track` into it.

        The first frame is awaited **before** spawning, because the command line
        needs the exact geometry and guessing it produces a skewed picture rather
        than an error.
        """
        if not ffmpeg_available():
            raise RuntimeError(
                "ffmpeg is not on PATH. The relay needs it to restream to RTMP; "
                "everything else works without it."
            )

        first = await track.recv()
        self._size = (first.width, first.height)
        args = build_args(self._target, first.width, first.height)

        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._pump = asyncio.create_task(self._run(track, first))
        asyncio.create_task(self._watch_stderr())

    async def _run(self, track: Any, first: Any) -> None:
        """Feed frames until the track ends or ffmpeg goes away."""
        assert self._proc is not None and self._proc.stdin is not None
        width, height = self._size or (first.width, first.height)
        frame = first
        try:
            while True:
                # Reformat every frame to the geometry the stream started at.
                # `reformat` is a no-op when it already matches, so the common
                # case costs nothing.
                if (frame.width, frame.height) != (
                    width,
                    height,
                ) or frame.format.name != "yuv420p":
                    frame = frame.reformat(width=width, height=height, format="yuv420p")
                self._proc.stdin.write(frame.to_ndarray().tobytes())
                await self._proc.stdin.drain()
                frame = await track.recv()
        except (BrokenPipeError, ConnectionResetError):
            # ffmpeg exited underneath us -- its stderr says why, and that is
            # already being captured.
            logger.info("restream %s: ffmpeg closed the pipe", self.label)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A track that ended is the normal way this stops (the host stopped
            # sharing), so it is not an error worth a stack trace.
            logger.info(
                "restream %s: source ended (%s)", self.label, type(exc).__name__
            )
        finally:
            await self._close_stdin()

    async def _watch_stderr(self) -> None:
        """Keep ffmpeg's complaints, because they are the only diagnosis there is.

        Kept as `self.error` rather than only logged: a broadcast that silently
        fails to appear on Twitch is the whole failure mode, and the host is
        looking at the pane, not at the relay's log.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        async for line in proc.stderr:
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            logger.warning("restream %s: %s", self.label, text)
            self.error = text

    async def _close_stdin(self) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.close()
            await proc.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass

    async def stop(self) -> None:
        """Stop the push, letting ffmpeg flush first.

        EOF on stdin is the graceful signal: ffmpeg finishes the current GOP and
        closes the RTMP session properly. Killing it instead leaves a corrupt
        tail on whatever recording the platform kept.
        """
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, Exception):
                pass
            self._pump = None

        proc = self._proc
        self._proc = None
        if proc is None:
            return
        await self._close_stdin()
        try:
            await asyncio.wait_for(proc.wait(), SHUTDOWN_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("restream %s: ffmpeg did not exit; killing it", self.label)
            try:
                proc.kill()
            except ProcessLookupError:
                pass


class Restreams:
    """Every RTMP push on this process, keyed by token.

    One per stream, not one per destination: a second destination means a second
    encode, and this is a 512MB shared-CPU box. Sending to several platforms at
    once is what a restreaming service is for, and that is a legitimate value for
    the custom destination.
    """

    def __init__(self) -> None:
        self._by_token: dict[str, Restream] = {}

    def get(self, token: str) -> Restream | None:
        return self._by_token.get(token)

    async def start(self, token: str, track: Any, target: str, label: str) -> Restream:
        await self.stop(token)
        push = Restream(token, target, label)
        await push.start(track)
        self._by_token[token] = push
        return push

    async def stop(self, token: str) -> bool:
        push = self._by_token.pop(token, None)
        if push is None:
            return False
        await push.stop()
        return True

    async def stop_all(self) -> None:
        for token in list(self._by_token):
            await self.stop(token)

    def __len__(self) -> int:
        return len(self._by_token)
