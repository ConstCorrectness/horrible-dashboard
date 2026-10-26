#!/usr/bin/env python3
"""Generator for HorribleAssault (.wav) audio assets.

Generates 24 kHz, 16-bit mono PCM .wav files in `apps/web/public/sounds/hassault/`.
24 kHz is load-bearing: the native client embeds these files and plays them at
`WAV_SAMPLE_RATE` (`apps/native-fps/src/audio.rs`); the browser resamples on decode.

1. Multi-surface walking footsteps (concrete, metal/duct, wood, sand, tile) + jump/land,
   four variants per surface: filtered-noise heel and toe contacts, granular grit and
   short modal resonances. No pitched sine "thumps" — a sine whose frequency sweeps
   through zero is the cartoon *boing* that made the old steps sound like stomping.
2. Multi-stage mechanical weapon reloads (magout, magin, bolt, full reload):
   metallic resonances, latch clicks and friction slides. Seeded, so deterministic.
3. Tactical radio player callouts (spotted, watch, danger, utility, reloading,
   fire_in_the_hole, flashbang, smoke_out, bomb_planted, bomb_defused):
   neural TTS (`edge-tts`, a backend dependency; needs network) decoded with
   `ffmpeg`, then a light walkie-talkie chain — PTT click, 300-3600 Hz band, mild
   compression, Roger beep. A formant synthesizer used to stand in here and is the
   reason the callouts sounded like a robot; there is deliberately no fallback to it.

Every sound is mastered to a per-category short-term A-weighted loudness (`master`)
so a footstep cannot be as loud as a reload just because both were peak-normalized.

    python tools/hassault_generate_sounds.py [out_dir] [--skip-voice]
"""

import asyncio
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 24000  # Fixed by the native client's WAV_SAMPLE_RATE; see the module docstring.

# The radio voice. Any `edge-tts --list-voices` name works; regenerate after changing.
VOICE = "en-US-GuyNeural"
VOICE_RATE = "+8%"

# Short-term loudness targets, dB(A) RMS over the loudest 100 ms window. The client
# then scales by distance and by the call site's gain, so these set the balance
# *between* categories: a radio line sits above a reload, which sits above a step.
LOUDNESS_DB = {
    "callout": -13.0,
    "reload": -18.0,
    "land": -20.0,
    "step": -22.0,
    "jump": -27.0,
}
PEAK_CEILING = 0.89  # -1 dBFS


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    """Write a float64 [-1, 1] array as 16-bit mono PCM WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def a_weighted(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """
    `x` filtered by the IEC 61672 A-weighting curve: roughly what the ear hears.

    Unweighted RMS is the wrong ruler for these sounds. The old footsteps were 80%
    energy below 150 Hz, which counts heavily in RMS and barely at all to the ear,
    so equal-RMS steps on two surfaces can differ by 5 dB in how loud they sound.
    """
    f2 = np.fft.rfftfreq(len(x), 1.0 / sr) ** 2
    ra = (12194.0**2 * f2**2) / (
        (f2 + 20.6**2) * np.sqrt((f2 + 107.7**2) * (f2 + 737.9**2)) * (f2 + 12194.0**2)
    )
    return np.fft.irfft(np.fft.rfft(x) * ra * 10.0 ** (2.0 / 20.0), len(x))


def short_term_rms(x: np.ndarray, sr: int = SR, window_s: float = 0.1) -> float:
    """A-weighted RMS of the loudest `window_s` window — how loud it *sounds*, not its tail."""
    y = a_weighted(x, sr)
    w = max(1, int(window_s * sr))
    if len(y) <= w:
        return float(np.sqrt(np.mean(y**2)))
    power = np.convolve(y**2, np.ones(w) / w, mode="valid")
    return float(np.sqrt(np.max(power)))


def master(x: np.ndarray, category: str, sr: int = SR) -> np.ndarray:
    """Scale to the category's loudness target, then keep the peak under the ceiling."""
    rms = short_term_rms(x, sr)
    if rms <= 1e-9:
        return x
    y = x * (10.0 ** (LOUDNESS_DB[category] / 20.0) / rms)
    peak = float(np.max(np.abs(y)))
    if peak > PEAK_CEILING:
        y *= PEAK_CEILING / peak
    return y


def biquad_bandpass(x: np.ndarray, f0: float, q: float, sr: int = SR) -> np.ndarray:
    """Direct-Form II Transposed 2nd-order bandpass filter."""
    w0 = 2.0 * np.pi * np.clip(f0, 20.0, sr * 0.48) / sr
    alpha = np.sin(w0) / (2.0 * max(q, 0.1))
    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1.0 + alpha
    a1 = -2.0 * np.cos(w0)
    a2 = 1.0 - alpha

    b0 /= a0
    b1 /= a0
    b2 /= a0
    a1 /= a0
    a2 /= a0

    y = np.zeros_like(x, dtype=np.float64)
    z1 = 0.0
    z2 = 0.0
    for i in range(len(x)):
        xi = float(x[i])
        yi = b0 * xi + z1
        z1 = b1 * xi - a1 * yi + z2
        z2 = b2 * xi - a2 * yi
        y[i] = yi
    return y


def biquad_lowpass(
    x: np.ndarray, f0: float, q: float = 0.707, sr: int = SR
) -> np.ndarray:
    """2nd-order Butterworth lowpass filter."""
    w0 = 2.0 * np.pi * np.clip(f0, 20.0, sr * 0.48) / sr
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    b0 = (1.0 - cos_w0) * 0.5
    b1 = 1.0 - cos_w0
    b2 = (1.0 - cos_w0) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b0 /= a0
    b1 /= a0
    b2 /= a0
    a1 /= a0
    a2 /= a0

    y = np.zeros_like(x, dtype=np.float64)
    z1 = 0.0
    z2 = 0.0
    for i in range(len(x)):
        xi = float(x[i])
        yi = b0 * xi + z1
        z1 = b1 * xi - a1 * yi + z2
        z2 = b2 * xi - a2 * yi
        y[i] = yi
    return y


def biquad_highpass(
    x: np.ndarray, f0: float, q: float = 0.707, sr: int = SR
) -> np.ndarray:
    """2nd-order Butterworth highpass filter."""
    w0 = 2.0 * np.pi * np.clip(f0, 20.0, sr * 0.48) / sr
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    b0 = (1.0 + cos_w0) * 0.5
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b0 /= a0
    b1 /= a0
    b2 /= a0
    a1 /= a0
    a2 /= a0

    y = np.zeros_like(x, dtype=np.float64)
    z1 = 0.0
    z2 = 0.0
    for i in range(len(x)):
        xi = float(x[i])
        yi = b0 * xi + z1
        z1 = b1 * xi - a1 * yi + z2
        z2 = b2 * xi - a2 * yi
        y[i] = yi
    return y


def exp_env(n: int, attack_s: float, decay_s: float, sr: int = SR) -> np.ndarray:
    """Fast linear attack followed by exponential decay."""
    t = np.arange(n, dtype=np.float64) / sr
    atk = max(attack_s, 1.0 / sr)
    env = np.where(t < atk, t / atk, np.exp(-(t - atk) / max(decay_s, 0.001)))
    return env


def place(
    dest: np.ndarray, src: np.ndarray, offset_s: float, gain: float = 1.0, sr: int = SR
) -> None:
    """Add `src * gain` into `dest` starting at `offset_s` seconds."""
    idx = int(round(offset_s * sr))
    if idx >= len(dest) or idx + len(src) <= 0:
        return
    s0 = max(0, -idx)
    d0 = max(0, idx)
    count = min(len(dest) - d0, len(src) - s0)
    if count > 0:
        dest[d0 : d0 + count] += src[s0 : s0 + count] * gain


# ---------------------------------------------------------------------------
# 1. Footsteps & Movement
# ---------------------------------------------------------------------------

STEP_VARIANTS = 4


def thud(rng: np.random.Generator, n: int, cutoff: float, decay_s: float) -> np.ndarray:
    """A soft, unpitched low impact: twice-lowpassed noise. Weight without a *boing*."""
    x = biquad_lowpass(biquad_lowpass(rng.standard_normal(n), cutoff), cutoff)
    return x / (np.std(x) + 1e-9) * 0.35 * exp_env(n, 0.0015, decay_s)


def burst(
    rng: np.random.Generator,
    n: int,
    f0: float,
    q: float,
    decay_s: float,
    attack_s: float = 0.0008,
) -> np.ndarray:
    """Band-limited noise transient: a click, a knock, a scuff, depending on band."""
    x = biquad_bandpass(rng.standard_normal(n), f0, q)
    return x / (np.std(x) + 1e-9) * 0.35 * exp_env(n, attack_s, decay_s)


def grains(
    rng: np.random.Generator,
    n: int,
    rate_hz: float,
    f0: float,
    q: float,
    attack_s: float,
    decay_s: float,
) -> np.ndarray:
    """
    Grit as a Poisson stream of tiny random clicks — sand, gravel, grit under a sole.

    Random in time *and* amplitude, which is the point: the previous sand step
    amplitude-modulated its noise with a 160 Hz sine, and a periodic modulation
    is a buzz, not a crunch.
    """
    clicks = np.zeros(n)
    count = rng.poisson(rate_hz * n / SR)
    idx = rng.integers(0, n, count)
    np.add.at(
        clicks, idx, rng.uniform(0.2, 1.0, count) * rng.choice([-1.0, 1.0], count)
    )
    k = np.exp(-np.arange(int(0.0015 * SR)) / (0.0004 * SR))
    x = biquad_bandpass(np.convolve(clicks, k)[:n], f0, q)
    return x / (np.std(x) + 1e-9) * 0.35 * exp_env(n, attack_s, decay_s)


def modes(n: int, table: list[tuple[float, float, float]], detune: float) -> np.ndarray:
    """Damped sinusoidal modes of a struck plate or plank."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for freq, dec, amp in table:
        out += amp * np.sin(2.0 * np.pi * freq * detune * t) * exp_env(n, 0.0008, dec)
    return out


def contact(
    rng: np.random.Generator, surface: str, n: int, v: dict[str, float]
) -> np.ndarray:
    """One boot contact (heel or toe) on a surface; `v` carries the variant's jitter."""
    d = v["detune"]
    if surface == "concrete":
        return (
            thud(rng, n, 190.0 * d, 0.016) * 0.55
            + burst(rng, n, 1050.0 * d, 1.1, 0.012) * 0.75
            + grains(rng, n, 2200.0, 3300.0 * d, 0.9, 0.002, 0.026) * 0.35
        )
    if surface == "sand":
        return (
            thud(rng, n, 150.0 * d, 0.018) * 0.35
            + grains(rng, n, 1600.0, 1700.0 * d, 0.7, 0.006, 0.045) * 0.95
            + grains(rng, n, 900.0, 3900.0 * d, 1.0, 0.008, 0.035) * 0.35
        )
    if surface == "wood":
        plank = [
            (140.0, 0.030, 0.55),
            (255.0, 0.024, 0.40),
            (470.0, 0.016, 0.22),
            (930.0, 0.010, 0.12),
        ]
        return (
            thud(rng, n, 210.0 * d, 0.020) * 0.50
            + modes(n, plank, d) * 0.30
            + burst(rng, n, 720.0 * d, 1.6, 0.010) * 0.60
        )
    if surface == "metal":
        plate = [
            (190.0, 0.050, 0.45),
            (338.0, 0.042, 0.40),
            (712.0, 0.032, 0.28),
            (1490.0, 0.026, 0.22),
            (2350.0, 0.020, 0.16),
        ]
        return (
            thud(rng, n, 220.0 * d, 0.016) * 0.40
            + modes(n, plate, d) * 0.30
            + burst(rng, n, 2300.0 * d, 2.4, 0.009) * 0.60
        )
    if surface == "tile":
        return (
            thud(rng, n, 210.0 * d, 0.012) * 0.40
            + burst(rng, n, 3000.0 * d, 1.8, 0.006) * 0.80
            + burst(rng, n, 1200.0 * d, 1.4, 0.011) * 0.45
        )
    raise ValueError(surface)


def synth_step(rng: np.random.Generator, surface: str) -> np.ndarray:
    """A heel strike then a lighter toe roll, every timing and band jittered per variant."""
    n = int(0.22 * SR)
    out = np.zeros(n)
    v = {"detune": rng.uniform(0.92, 1.08)}
    place(out, contact(rng, surface, int(0.14 * SR), v), 0.0)
    place(
        out,
        contact(rng, surface, int(0.12 * SR), v),
        rng.uniform(0.040, 0.070),
        rng.uniform(0.40, 0.60),
    )
    # No sub-rumble: below 70 Hz is felt, not heard, and it is what read as stomping.
    return master(biquad_highpass(out, 70.0), "step")


def synth_land(rng: np.random.Generator) -> np.ndarray:
    """Two boots landing 20 ms apart, with a rattle of kit."""
    n = int(0.30 * SR)
    out = np.zeros(n)
    v = {"detune": 1.0}
    place(out, thud(rng, n, 160.0, 0.040) * 0.7 + contact(rng, "concrete", n, v), 0.0)
    place(out, contact(rng, "concrete", n, {"detune": 1.06}) * 0.7, 0.021)
    place(
        out, grains(rng, int(0.12 * SR), 700.0, 2600.0, 1.4, 0.006, 0.040) * 0.35, 0.018
    )
    return master(biquad_highpass(out, 55.0), "land")


def synth_jump(rng: np.random.Generator) -> np.ndarray:
    """A push-off scuff and a little cloth — quiet, a cue rather than an event."""
    n = int(0.16 * SR)
    out = (
        burst(rng, n, 1100.0, 1.2, 0.022, 0.003) * 0.6
        + grains(rng, n, 900.0, 2800.0, 1.0, 0.010, 0.040) * 0.5
    )
    return master(biquad_highpass(out, 90.0), "jump")


# ---------------------------------------------------------------------------
# 2. Mechanical Weapon Reloads
# ---------------------------------------------------------------------------


def synth_reload_magout(rng: np.random.Generator) -> np.ndarray:
    """Magazine release catch click followed by polymer/metal magazine slide-out."""
    dur = 0.28
    n = int(dur * SR)
    out = np.zeros(n)

    # 1. Metallic mag-catch button click at t = 0.005s
    n_click = int(0.06 * SR)
    t_c = np.arange(n_click) / SR
    click = (
        np.sin(2.0 * np.pi * 2450.0 * t_c) * exp_env(n_click, 0.0005, 0.008) * 0.6
        + biquad_bandpass(rng.standard_normal(n_click), 3400.0, 4.0)
        * exp_env(n_click, 0.0005, 0.007)
        * 0.7
    )
    place(out, click, 0.005)

    # 2. Magazine sliding out of receiver well (sweeping resonant friction t = 0.035..0.19s)
    n_slide = int(0.16 * SR)
    t_s = np.arange(n_slide) / SR
    noise = rng.standard_normal(n_slide)
    slide_lo = biquad_bandpass(noise, 1350.0, 2.6) * np.sin(np.pi * t_s / 0.16) ** 1.3
    slide_hi = biquad_bandpass(noise, 2900.0, 3.2) * exp_env(n_slide, 0.025, 0.045)
    lip_pop = np.sin(2.0 * np.pi * 420.0 * t_s) * exp_env(n_slide, 0.002, 0.015)
    place(out, slide_lo * 0.65 + slide_hi * 0.45 + lip_pop * 0.35, 0.035)

    return out * 0.92


def synth_reload_magin(rng: np.random.Generator) -> np.ndarray:
    """Firm magazine well alignment scrape, heavy seating thud, and spring latch snap."""
    dur = 0.30
    n = int(dur * SR)
    out = np.zeros(n)

    # 1. Brief magwell lip alignment scrape (t = 0.00s)
    n_scr = int(0.07 * SR)
    scrape = biquad_bandpass(rng.standard_normal(n_scr), 1950.0, 3.0) * exp_env(
        n_scr, 0.004, 0.020
    )
    place(out, scrape * 0.45, 0.0)

    # 2. Heavy receiver seating thud (t = 0.055s)
    n_thud = int(0.14 * SR)
    t_th = np.arange(n_thud) / SR
    thud_body = np.sin(2.0 * np.pi * 135.0 * t_th * np.exp(-t_th * 12.0)) * exp_env(
        n_thud, 0.001, 0.028
    )
    thud_mid = biquad_bandpass(rng.standard_normal(n_thud), 980.0, 2.2) * exp_env(
        n_thud, 0.001, 0.022
    )
    place(out, thud_body * 0.78 + thud_mid * 0.62, 0.055)

    # 3. Sharp steel mag-catch locking snap (t = 0.095s)
    n_lock = int(0.12 * SR)
    t_lk = np.arange(n_lock) / SR
    lock_ring = (
        np.sin(2.0 * np.pi * 2680.0 * t_lk) * exp_env(n_lock, 0.0005, 0.014) * 0.55
        + np.sin(2.0 * np.pi * 4120.0 * t_lk) * exp_env(n_lock, 0.0005, 0.010) * 0.38
    )
    lock_snap = biquad_bandpass(rng.standard_normal(n_lock), 3100.0, 3.8) * exp_env(
        n_lock, 0.0008, 0.016
    )
    place(out, lock_ring + lock_snap * 0.75, 0.092)

    return out * 0.95


def synth_reload_bolt(rng: np.random.Generator) -> np.ndarray:
    """Crisp steel bolt carrier rack pull and spring-driven forward battery lock clack."""
    dur = 0.34
    n = int(dur * SR)
    out = np.zeros(n)

    # 1. Bolt pull back: metallic rail slide + sear click (t = 0.00s)
    n_pull = int(0.13 * SR)
    t_p = np.arange(n_pull) / SR
    pull_slide = biquad_bandpass(rng.standard_normal(n_pull), 2650.0, 3.5) * exp_env(
        n_pull, 0.008, 0.032
    )
    pull_ring = np.sin(2.0 * np.pi * 1880.0 * t_p) * exp_env(n_pull, 0.002, 0.020)
    place(out, pull_slide * 0.68 + pull_ring * 0.40, 0.0)

    # 2. Bolt release slam forward into battery (t = 0.115s) - unmistakable CS 1.6 metallic clack
    n_slam = int(0.20 * SR)
    t_s = np.arange(n_slam) / SR
    slam_impact = biquad_bandpass(rng.standard_normal(n_slam), 3350.0, 3.6) * exp_env(
        n_slam, 0.0008, 0.024
    )
    slam_low = np.sin(2.0 * np.pi * 210.0 * t_s * np.exp(-t_s * 16.0)) * exp_env(
        n_slam, 0.001, 0.022
    )
    # Steel chamber ring modes
    ring = (
        0.50 * np.sin(2.0 * np.pi * 2420.0 * t_s) * exp_env(n_slam, 0.0008, 0.028)
        + 0.38 * np.sin(2.0 * np.pi * 3780.0 * t_s) * exp_env(n_slam, 0.0008, 0.022)
        + 0.25 * np.sin(2.0 * np.pi * 5120.0 * t_s) * exp_env(n_slam, 0.0008, 0.015)
    )
    place(out, slam_impact * 0.75 + slam_low * 0.55 + ring * 0.70, 0.115)

    return out * 0.95


def synth_reload_full(rng: np.random.Generator) -> np.ndarray:
    """Combined 3rd-person reload cue (mag latch + rack click) for remote NoiseEvents."""
    dur = 0.42
    n = int(dur * SR)
    out = np.zeros(n)
    magin = synth_reload_magin(rng)
    bolt = synth_reload_bolt(rng)
    place(out, magin, 0.0, 0.75)
    place(out, bolt, 0.14, 0.85)
    return out


# ---------------------------------------------------------------------------
# 3. Tactical Radio Player Callouts
# ---------------------------------------------------------------------------

CALLOUT_LINES: dict[str, str] = {
    "callout_spotted": "Enemy spotted!",
    "callout_watch": "Holding this angle!",
    "callout_danger": "Danger! Fall back!",
    "callout_utility": "Need utility here!",
    # Short: this one plays on every one of your own reloads.
    "callout_reloading": "Reloading!",
    "callout_fire_in_the_hole": "Fire in the hole!",
    "callout_flashbang": "Flashbang out!",
    "callout_smoke_out": "Smoke out!",
    "callout_bomb_planted": "The bomb has been planted.",
    "callout_bomb_defused": "Counter-Terrorists win. Bomb defused.",
}


def tts(text: str, sr: int = SR) -> np.ndarray:
    """Speak `text` with the neural `VOICE`, decoded to float mono at `sr` by ffmpeg."""
    import edge_tts  # backend dependency; imported here so --skip-voice needs neither it nor network

    async def fetch() -> bytes:
        chunks: list[bytes] = []
        async for part in edge_tts.Communicate(text, VOICE, rate=VOICE_RATE).stream():
            if part["type"] == "audio":
                chunks.append(part["data"])
        return b"".join(chunks)

    mp3 = asyncio.run(fetch())
    if not mp3:
        raise RuntimeError(f"edge-tts returned no audio for {text!r}")
    pcm = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(sr),
            "pipe:1",
        ],
        input=mp3,
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(pcm, dtype=np.float32).astype(np.float64)


def tighten_gaps(x: np.ndarray, sr: int = SR, max_gap_s: float = 0.12) -> np.ndarray:
    """
    Shorten pauses between sentences to `max_gap_s`.

    TTS reads "Danger! Fall back!" with a narrator's half-second breath between
    the two; a shout over the radio has none, and a 2 s callout is still playing
    when the next thing happens.
    """
    win = max(1, int(0.010 * sr))
    env = np.convolve(np.abs(x), np.ones(win) / win, mode="same")
    quiet = env < 0.02 * np.max(env)
    keep = np.ones(len(x), dtype=bool)
    max_gap = int(max_gap_s * sr)
    i = 0
    while i < len(x):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < len(x) and quiet[j]:
            j += 1
        if 0 < i and j < len(x) and j - i > max_gap:
            # Keep the edges of the gap, so each word still decays into it.
            keep[i + max_gap // 2 : j - max_gap // 2] = False
        i = j
    return x[keep]


def apply_military_radio_dsp(
    voice_pcm: np.ndarray, rng: np.random.Generator, sr: int = SR
) -> np.ndarray:
    """
    Frame clean speech as a team-radio transmission: PTT click, band, beep.

    Deliberately light. The band is what says "radio"; the old chain also drove the
    voice into `tanh` at 2.4x, and on top of a formant synthesizer that distortion
    was most of the robot.
    """
    # Trim leading/trailing silence
    abs_v = np.abs(voice_pcm)
    thresh = max(np.max(abs_v) * 0.03, 1e-4)
    nonzero = np.where(abs_v > thresh)[0]
    if len(nonzero) > 0:
        start = max(0, int(nonzero[0]) - int(0.015 * sr))
        end = min(len(voice_pcm), int(nonzero[-1]) + int(0.040 * sr))
        voice_pcm = voice_pcm[start:end]

    peak = np.max(np.abs(voice_pcm))
    if peak > 1e-5:
        voice_pcm = voice_pcm / peak
    voice_pcm = tighten_gaps(voice_pcm, sr)

    # 1. Radio passband, 4th order each side (two cascaded biquads), with a little presence.
    bp = biquad_highpass(biquad_highpass(voice_pcm, 300.0, 0.707, sr), 300.0, 0.707, sr)
    bp = biquad_lowpass(biquad_lowpass(bp, 3600.0, 0.707, sr), 3600.0, 0.707, sr)
    bp = bp + biquad_bandpass(bp, 2000.0, 1.0, sr) * 0.25

    # 2. Mild AGC-style compression: evens out syllables without audible distortion.
    drive = 1.4
    bp = np.tanh(bp / (np.max(np.abs(bp)) + 1e-9) * drive) / np.tanh(drive)

    # 3. Frame with PTT key-on (28 ms), speech, and Roger beep + squelch tail (70 ms).
    pre_n = int(0.028 * sr)
    post_n = int(0.070 * sr)
    out = np.zeros(pre_n + len(bp) + post_n, dtype=np.float64)

    t_pre = np.arange(pre_n) / sr
    out[:pre_n] = (
        biquad_bandpass(rng.standard_normal(pre_n), 1900.0, 2.2, sr)
        * exp_env(pre_n, 0.001, 0.008, sr)
        * 0.20
        + np.sin(2.0 * np.pi * 1200.0 * t_pre)
        * exp_env(pre_n, 0.0005, 0.004, sr)
        * 0.12
    )

    carrier_hiss = (
        biquad_bandpass(rng.standard_normal(len(bp)), 1800.0, 0.8, sr) * 0.008
    )
    out[pre_n : pre_n + len(bp)] = bp + carrier_hiss

    t_post = np.arange(post_n) / sr
    gate = np.clip((t_post - 0.012) / 0.002, 0, 1) * np.clip(
        (0.034 - t_post) / 0.002, 0, 1
    )
    roger_beep = np.sin(2.0 * np.pi * 2150.0 * t_post) * gate * 0.10
    squelch_tail = (
        biquad_bandpass(rng.standard_normal(post_n), 2100.0, 1.5, sr)
        * exp_env(post_n, 0.002, 0.015, sr)
        * 0.12
    )
    out[pre_n + len(bp) :] = roger_beep + squelch_tail

    return out


def generate_callouts(out_dir: Path, rng: np.random.Generator) -> None:
    """Speak each callout with neural TTS and frame it as a radio transmission."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg is required to decode the TTS audio (or pass --skip-voice)"
        )
    for name, text in CALLOUT_LINES.items():
        processed = master(apply_military_radio_dsp(tts(text), rng), "callout")
        p = out_dir / f"{name}.wav"
        write_wav(p, processed, SR)
        print(
            f"  [wav] {name:26s} ({len(processed) / SR:.2f}s, {p.stat().st_size // 1024:2d} KB) -> '{text}'"
        )


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skip_voice = "--skip-voice" in sys.argv[1:]
    here = Path(__file__).resolve().parent
    out_dir = (
        Path(args[0])
        if args
        else here.parent / "apps" / "web" / "public" / "sounds" / "hassault"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(42)

    print(f"Generating HorribleAssault .wav sound suite in {out_dir} ...")

    # 1. Multi-surface walking footsteps & movement
    step_files: dict[str, np.ndarray] = {}
    for surface in ("concrete", "metal", "wood", "sand", "tile"):
        for variant in range(1, STEP_VARIANTS + 1):
            step_files[f"step_{surface}_{variant}"] = synth_step(rng, surface)
    step_files["land"] = synth_land(rng)
    step_files["jump"] = synth_jump(rng)
    for name, data in step_files.items():
        p = out_dir / f"{name}.wav"
        write_wav(p, data, SR)
        print(
            f"  [wav] {name:26s} ({len(data) / SR:.2f}s, {p.stat().st_size // 1024:2d} KB)"
        )

    # 2. Mechanical weapon reloads
    reload_files = {
        "reload_magout": synth_reload_magout(rng),
        "reload_magin": synth_reload_magin(rng),
        "reload_bolt": synth_reload_bolt(rng),
        "reload": synth_reload_full(rng),
    }
    for name, data in reload_files.items():
        p = out_dir / f"{name}.wav"
        write_wav(p, master(data, "reload"), SR)
        print(
            f"  [wav] {name:26s} ({len(data) / SR:.2f}s, {p.stat().st_size // 1024:2d} KB)"
        )

    # 3. Tactical radio player callouts
    if skip_voice:
        print("  (callouts skipped)")
    else:
        generate_callouts(out_dir, rng)
    print("Done.")


if __name__ == "__main__":
    main()
