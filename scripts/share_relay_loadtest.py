"""Load-test the share relay: one synthetic publisher, N real WHEP viewers.

Answers the question the relay's viewer ceiling depends on -- "does every viewer
still get the host's frame rate at N?" -- with numbers instead of a guess:

    uv run --no-sync python scripts/share_relay_loadtest.py \
        --relay http://127.0.0.1:9101 --viewers 15 --pin-cores 2

What it does:

1. Mints a stream (`POST /streams`, with `--key` when the relay is gated).
2. Publishes a synthetic desktop over WHIP from its own process: a high-contrast
   "text" texture with a scrolling band and a moving bar, so the encoder has real
   work and every frame differs.
3. Waits for the relay to report `live`, then opens `--viewers` WHEP viewers
   spread over `--procs` processes (one Python process cannot depacketize and
   decode fifteen 1080p streams without becoming the bottleneck itself).
4. Each viewer counts decoded video frames; the measurement window starts after a
   warm-up, so connection setup does not dilute the frame rate.
5. Samples the relay process's CPU and RSS when it runs on this machine (found by
   its listening port), and reads `/health` -> `load` when the relay reports one.

`--pin-cores 2` restricts the local relay to two CPUs for the run, which is what
the Fly performance-2x machine has; without it a 32-core workstation hides the
ceiling entirely.

Exit status is 0 when every viewer connected, got a first frame within
`--max-ttff`, and ran at no less than `--min-ratio` of the publisher's rate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Run from anywhere: the jitter patch lives in the backend package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VIDEO_TIME_BASE = Fraction(1, 90000)


# -- ICE -----------------------------------------------------------------------


def _aiortc_ice(entries: list[dict[str, Any]]) -> list[Any]:
    """The relay's browser ICE JSON, reduced to what aiortc will actually use."""
    from aiortc import RTCIceServer

    servers = []
    for entry in entries:
        urls = entry.get("urls") or []
        urls = [urls] if isinstance(urls, str) else list(urls)
        stun = [u for u in urls if u.startswith("stun:")]
        turn = [
            u
            for u in urls
            if u.startswith("turn:") and "transport=tcp" not in u and ":53" not in u
        ]
        if stun:
            servers.append(RTCIceServer(urls=stun[:1]))
        if turn and entry.get("username"):
            servers.append(
                RTCIceServer(
                    urls=turn[:1],
                    username=entry["username"],
                    credential=entry.get("credential", ""),
                )
            )
    return servers


async def _fetch_ice(client: Any, url: str, use_ice: bool) -> list[Any]:
    if not use_ice:
        return []
    try:
        res = await client.get(url)
        res.raise_for_status()
        return _aiortc_ice(res.json().get("iceServers") or [])
    except Exception:
        return []


# -- publisher -----------------------------------------------------------------


def _synthetic_track(width: int, height: int, fps: int, counter: list[int]) -> Any:
    import numpy as np
    from aiortc import VideoStreamTrack
    from av import VideoFrame

    rng = np.random.default_rng(7)
    # 8x8 blocks of near-black/near-white: as hard on an encoder as a page of text.
    blocks = rng.integers(0, 2, (height // 8 + 1, width // 8 + 1), dtype=np.uint8)
    luma = np.kron(blocks, np.ones((8, 8), dtype=np.uint8))[:height, :width] * 200 + 30
    chroma = np.full((height // 2, width), 128, dtype=np.uint8)
    band = slice(height // 3, 2 * height // 3)

    class Synthetic(VideoStreamTrack):
        def __init__(self) -> None:
            super().__init__()
            self._n = 0
            self._start: float | None = None

        async def recv(self) -> Any:
            if self._start is None:
                self._start = time.monotonic()
            due = self._start + self._n / fps
            delay = due - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            y = luma.copy()
            # A scrolling band (text scrolling in an editor) and a moving bar.
            y[band] = np.roll(luma[band], -6 * self._n, axis=1)
            x = (self._n * 12) % (width - 40)
            y[:, x : x + 40] = 255
            frame = VideoFrame.from_ndarray(np.vstack([y, chroma]), format="yuv420p")
            frame.pts = int(self._n / fps / VIDEO_TIME_BASE)
            frame.time_base = VIDEO_TIME_BASE
            self._n += 1
            counter[0] = self._n
            return frame

    return Synthetic()


async def _publish(
    relay: str,
    token: str,
    width: int,
    height: int,
    fps: int,
    until: float,
    use_ice: bool,
    audio: bool,
) -> dict[str, Any]:
    import httpx
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    counter = [0]
    async with httpx.AsyncClient(timeout=30) as client:
        ice = await _fetch_ice(client, f"{relay}/whip/{token}/ice", use_ice)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice))
        pc.addTransceiver(
            _synthetic_track(width, height, fps, counter), direction="sendonly"
        )
        if audio:
            # A browser share usually carries system audio, and audio is still a
            # per-viewer Opus encode on the relay -- so the test carries it too.
            from aiortc.mediastreams import AudioStreamTrack

            pc.addTransceiver(AudioStreamTrack(), direction="sendonly")
        await pc.setLocalDescription(await pc.createOffer())
        res = await client.post(
            f"{relay}/whip/{token}",
            content=pc.localDescription.sdp,
            headers={"Content-Type": "application/sdp"},
        )
        res.raise_for_status()
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=res.text, type="answer")
        )
        started = time.time()
        while time.time() < until:
            await asyncio.sleep(0.5)
        sent = counter[0]
        elapsed = time.time() - started
        try:
            await client.delete(f"{relay}/whip/{token}")
        except Exception:
            pass
        await asyncio.wait_for(pc.close(), 5)
    return {"frames": sent, "fps": sent / elapsed if elapsed else 0.0}


def publisher_proc(*args: Any) -> dict[str, Any]:
    return asyncio.run(_publish(*args))


# -- viewers -------------------------------------------------------------------


async def _view(
    client: Any,
    relay: str,
    code: str,
    index: int,
    t_measure: float,
    t_end: float,
    use_ice: bool,
) -> dict[str, Any]:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    began = time.time()
    out: dict[str, Any] = {"viewer": index, "status": None, "ttff": None, "fps": 0.0}
    ice = await _fetch_ice(client, f"{relay}/whep/{code}/ice", use_ice)
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    stamps: list[float] = []
    heard: list[float] = []
    consumers: list[asyncio.Task] = []

    @pc.on("track")
    def _on_track(track: Any) -> None:
        async def consume() -> None:
            try:
                while True:
                    await track.recv()
                    if track.kind == "video":
                        stamps.append(time.time())
                    else:
                        heard.append(time.time())
            except Exception:
                pass

        consumers.append(asyncio.ensure_future(consume()))

    location = None
    try:
        await pc.setLocalDescription(await pc.createOffer())
        res = await client.post(
            f"{relay}/whep/{code}",
            content=pc.localDescription.sdp,
            headers={"Content-Type": "application/sdp"},
        )
        out["status"] = res.status_code
        if res.status_code == 201:
            location = res.headers.get("location")
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=res.text, type="answer")
            )
            while time.time() < t_end:
                await asyncio.sleep(0.25)
    finally:
        if stamps:
            out["ttff"] = stamps[0] - began
        window = [s for s in stamps if t_measure <= s <= t_end]
        span = t_end - t_measure
        out["fps"] = len(window) / span if span > 0 else 0.0
        out["frames"] = len(stamps)
        heard_in = [s for s in heard if t_measure <= s <= t_end]
        out["audio_fps"] = len(heard_in) / span if span > 0 else 0.0
        if location:
            # A well-behaved viewer gives its seat back. Absent on relays that
            # predate WHEP DELETE, where the seat frees on ICE consent expiry.
            try:
                await client.delete(
                    relay + location if location.startswith("/") else location
                )
            except Exception:
                pass
        for task in consumers:
            task.cancel()
        try:
            await asyncio.wait_for(pc.close(), 5)
        except Exception:
            pass
    return out


async def _viewers(
    relay: str,
    code: str,
    indices: list[int],
    t_measure: float,
    t_end: float,
    use_ice: bool,
    stagger: float,
) -> list[dict[str, Any]]:
    import httpx

    from backend.share_relay import jitter

    jitter.install()
    async with httpx.AsyncClient(timeout=60) as client:

        async def one(n: int, i: int) -> dict[str, Any]:
            await asyncio.sleep(n * stagger)
            return await _view(client, relay, code, i, t_measure, t_end, use_ice)

        return await asyncio.gather(*(one(n, i) for n, i in enumerate(indices)))


def viewers_proc(*args: Any) -> list[dict[str, Any]]:
    return asyncio.run(_viewers(*args))


# -- relay process sampling ----------------------------------------------------


def _local_relay_process(relay: str) -> Any:
    import psutil

    parsed = urlparse(relay)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    # Prefer the listener, but a sandboxed launcher can report it as pid 0; the
    # server side of our own open (keep-alive) connection names the same process.
    found = [
        conn
        for conn in psutil.net_connections(kind="tcp")
        if conn.laddr and conn.laddr.port == port and conn.pid
    ]
    found.sort(key=lambda c: c.status != psutil.CONN_LISTEN)
    return psutil.Process(found[0].pid) if found else None


# -- main ----------------------------------------------------------------------


async def main(args: argparse.Namespace) -> int:
    import httpx

    relay = args.relay.rstrip("/")
    headers = {"X-Relay-Key": args.key} if args.key else {}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        res = await client.post(
            f"{relay}/streams", json={"title": "loadtest", "ttl_s": 900}
        )
        res.raise_for_status()
        minted = res.json()
        token, code = minted["token"], minted["code"]
        print(f"minted stream code={code}")

        proc = _local_relay_process(relay)
        original_affinity = None
        if proc is not None and args.pin_cores:
            original_affinity = proc.cpu_affinity()
            proc.cpu_affinity(original_affinity[: args.pin_cores])
            print(f"relay pid {proc.pid} pinned to {args.pin_cores} cores")
            # Keep the test's own publisher and viewers off those cores: they
            # decode as many streams as the relay serves, and sharing its cores
            # would measure contention with the harness, not the relay.
            rest = original_affinity[args.pin_cores :]
            if rest:
                psutil_self = __import__("psutil").Process()
                psutil_self.cpu_affinity(rest)
        elif args.pin_cores:
            print(
                "WARNING: relay process not found; it is NOT pinned and CPU is not sampled"
            )

        loop = asyncio.get_running_loop()
        per_proc = max(1, -(-args.viewers // args.procs))
        groups = [
            list(range(i, min(i + per_proc, args.viewers)))
            for i in range(0, args.viewers, per_proc)
        ]
        pool = ProcessPoolExecutor(max_workers=len(groups) + 1)
        try:
            t_pub_end = (
                time.time() + args.connect_budget + args.warmup + args.seconds + 10
            )
            pub = loop.run_in_executor(
                pool,
                publisher_proc,
                relay,
                token,
                args.width,
                args.height,
                args.fps,
                t_pub_end,
                not args.no_ice,
                not args.no_audio,
            )
            # Viewers are only let in once a frame has decoded on the relay.
            deadline = time.time() + args.connect_budget
            while time.time() < deadline:
                st = (await client.get(f"{relay}/streams/{token}")).json()
                if st.get("live"):
                    break
                await asyncio.sleep(0.5)
            else:
                print("FAIL: the relay never reported the publisher live")
                return 2
            print("publisher live; starting viewers")

            t_measure = time.time() + args.warmup
            t_end = t_measure + args.seconds
            jobs = [
                loop.run_in_executor(
                    pool,
                    viewers_proc,
                    relay,
                    code,
                    group,
                    t_measure,
                    t_end,
                    not args.no_ice,
                    args.stagger,
                )
                for group in groups
            ]

            cpu: list[float] = []
            rss: list[float] = []
            health_load: dict[str, Any] = {}
            if proc is not None:
                proc.cpu_percent(None)
            while time.time() < t_end:
                await asyncio.sleep(1.0)
                if proc is not None and time.time() >= t_measure:
                    cpu.append(proc.cpu_percent(None))
                    rss.append(proc.memory_info().rss / 2**20)
                if time.time() >= t_measure + args.seconds / 2 and not health_load:
                    try:
                        health_load = (await client.get(f"{relay}/health")).json().get(
                            "load"
                        ) or {}
                    except Exception:
                        pass

            results = [r for group in await asyncio.gather(*jobs) for r in group]
            published = await pub
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            if original_affinity is not None:
                proc.cpu_affinity(original_affinity)
            try:
                await client.delete(f"{relay}/streams/{token}")
            except Exception:
                pass

    pub_fps = published["fps"]
    print(f"\npublisher: {published['frames']} frames, {pub_fps:.1f} fps")
    print(f"{'viewer':>6} {'status':>6} {'ttff_s':>7} {'fps':>6} {'ratio':>6} {'audio/s':>7}")
    ok = True
    for r in sorted(results, key=lambda r: r["viewer"]):
        ratio = r["fps"] / pub_fps if pub_fps else 0.0
        ttff = f"{r['ttff']:.1f}" if r["ttff"] is not None else "-"
        good = (
            r["status"] == 201
            and r["ttff"] is not None
            and r["ttff"] <= args.max_ttff
            and ratio >= args.min_ratio
        )
        ok &= good
        print(
            f"{r['viewer']:>6} {r['status']!s:>6} {ttff:>7} {r['fps']:>6.1f} {ratio:>6.2f} {r.get('audio_fps', 0):>7.1f}"
            + ("" if good else "  <-- FAIL")
        )
    fps_all = [r["fps"] for r in results]
    print(
        f"\nviewer fps: min {min(fps_all):.1f}  median {statistics.median(fps_all):.1f}"
        f"  max {max(fps_all):.1f}"
    )
    if cpu:
        cores = args.pin_cores or os.cpu_count()
        print(
            f"relay cpu: mean {statistics.mean(cpu):.0f}%  max {max(cpu):.0f}%"
            f"  (100% = one core; pinned to {cores})"
        )
        print(f"relay rss: max {max(rss):.0f} MB")
    if health_load:
        print(f"relay /health load: {json.dumps(health_load)}")
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--relay", default="http://127.0.0.1:9101")
    p.add_argument("--key", default=os.environ.get("SHARE_RELAY_KEY", ""))
    p.add_argument("--viewers", type=int, default=15)
    p.add_argument("--procs", type=int, default=3)
    p.add_argument("--seconds", type=float, default=30.0, help="measurement window")
    p.add_argument(
        "--warmup", type=float, default=15.0, help="connect time excluded from fps"
    )
    p.add_argument("--connect-budget", type=float, default=30.0)
    p.add_argument(
        "--stagger", type=float, default=0.2, help="seconds between viewer joins"
    )
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=15)
    p.add_argument(
        "--pin-cores", type=int, default=0, help="limit a local relay to N cores"
    )
    p.add_argument(
        "--no-ice", action="store_true", help="host candidates only (same machine)"
    )
    p.add_argument("--no-audio", action="store_true", help="publish video only")
    p.add_argument("--max-ttff", type=float, default=10.0)
    p.add_argument("--min-ratio", type=float, default=0.9)
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
