#!/usr/bin/env python3
"""Deterministic generator for HorribleAssault (.wav) audio assets.

Generates 24 kHz, 16-bit mono PCM .wav files in `apps/web/public/sounds/hassault/`:
1. Multi-surface walking footsteps (concrete, metal/duct, wood, sand, tile) + jump/land:
   Synthesized using physical modal synthesis, multi-band impact transients, and
   surface-specific granular friction envelopes.
2. Multi-stage mechanical weapon reloads (magout, magin, bolt, full reload):
   Synthesized using metallic waveguide resonances, latch clicks, and friction slides.
3. Tactical radio player callouts (spotted, watch, danger, utility, reloading,
   fire_in_the_hole, flashbang, smoke_out, bomb_planted, bomb_defused):
   Synthesized via neural TTS (`edge-tts`) when available (with formant-synthesized
   fallback) processed through a military walkie-talkie DSP chain:
   - 2 ms PTT key-on click
   - 300-3400 Hz 4th-order Butterworth radio bandpass filter
   - Soft tanh walkie-talkie saturation + subtle carrier noise
   - Two-tone NATO Roger beep + squelch tail
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 24000  # 24 kHz sample rate (12 kHz Nyquist: crisp transients, compact ~25 KB/file)


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    """Write normalized float64 [-1, 1] array as 16-bit mono PCM WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = np.max(np.abs(samples))
    if peak > 0.98:
        samples = samples * (0.98 / peak)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


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


def biquad_lowpass(x: np.ndarray, f0: float, q: float = 0.707, sr: int = SR) -> np.ndarray:
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


def biquad_highpass(x: np.ndarray, f0: float, q: float = 0.707, sr: int = SR) -> np.ndarray:
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


def place(dest: np.ndarray, src: np.ndarray, offset_s: float, gain: float = 1.0, sr: int = SR) -> None:
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

def synth_step_concrete(rng: np.random.Generator, variant: int) -> np.ndarray:
    """Tactical boot heel-toe strike on gritty concrete pavement."""
    dur = 0.24
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # Heel impact thud (low-frequency floor impulse)
    f_heel = 92.0 if variant == 1 else 86.0
    heel_body = np.sin(2.0 * np.pi * f_heel * t * np.exp(-t * 14.0)) * exp_env(n, 0.002, 0.032)
    heel_grit = biquad_bandpass(rng.standard_normal(n), 780.0 + variant * 60.0, 1.4) * exp_env(n, 0.0015, 0.028)
    heel_scuff = biquad_bandpass(rng.standard_normal(n), 2400.0, 1.8) * exp_env(n, 0.003, 0.022)
    place(out, heel_body * 0.65 + heel_grit * 0.55 + heel_scuff * 0.35, 0.0)

    # Toe roll-off (~48 ms later) + micro-gravel crunch
    n2 = int(0.16 * SR)
    t2 = np.arange(n2) / SR
    toe_body = np.sin(2.0 * np.pi * (125.0 + variant * 8.0) * t2 * np.exp(-t2 * 18.0)) * exp_env(n2, 0.003, 0.025)
    toe_grit = biquad_bandpass(rng.standard_normal(n2), 1450.0 - variant * 90.0, 1.6) * exp_env(n2, 0.004, 0.035)
    place(out, toe_body * 0.42 + toe_grit * 0.48, 0.046 + variant * 0.004)

    return out * 0.9


def synth_step_metal(rng: np.random.Generator, variant: int) -> np.ndarray:
    """Resonant sheet-metal ventilation duct / catwalk impact."""
    dur = 0.30
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # Sheet-metal inharmonic modal plate partials (hollow duct boom + high metallic ring)
    modes = (
        [(195.0, 0.075, 0.50), (342.0, 0.065, 0.45), (718.0, 0.050, 0.32), (1490.0, 0.042, 0.28), (2340.0, 0.035, 0.22), (3680.0, 0.024, 0.15)]
        if variant == 1
        else [(184.0, 0.078, 0.50), (328.0, 0.062, 0.44), (685.0, 0.052, 0.34), (1560.0, 0.040, 0.26), (2490.0, 0.032, 0.20), (3890.0, 0.022, 0.14)]
    )
    modal = np.zeros(n)
    for freq, dec, amp in modes:
        # Slight pitch bend on initial sheet-metal flex
        f_inst = freq * (1.0 + 0.025 * np.exp(-t * 45.0))
        modal += amp * np.sin(2.0 * np.pi * np.cumsum(f_inst) / SR) * exp_env(n, 0.001, dec)

    # Sharp metallic boot clank transient
    clank = biquad_bandpass(rng.standard_normal(n), 2150.0 + variant * 120.0, 3.2) * exp_env(n, 0.001, 0.024)
    scuff = biquad_bandpass(rng.standard_normal(n), 4200.0, 2.5) * exp_env(n, 0.002, 0.016)
    place(out, modal * 0.65 + clank * 0.55 + scuff * 0.28, 0.0)
    return out * 0.92


def synth_step_wood(rng: np.random.Generator, variant: int) -> np.ndarray:
    """Hollow timber plank / crate footstep with warm woody resonance."""
    dur = 0.25
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # Wood plank resonant modes (warm hollow 140-480 Hz)
    modes = (
        [(135.0, 0.048, 0.60), (248.0, 0.038, 0.45), (465.0, 0.026, 0.30), (920.0, 0.018, 0.18)]
        if variant == 1
        else [(128.0, 0.050, 0.60), (262.0, 0.036, 0.42), (490.0, 0.025, 0.28), (980.0, 0.016, 0.16)]
    )
    plank = np.zeros(n)
    for freq, dec, amp in modes:
        plank += amp * np.sin(2.0 * np.pi * freq * t) * exp_env(n, 0.0015, dec)

    knock = biquad_bandpass(rng.standard_normal(n), 640.0 + variant * 45.0, 2.4) * exp_env(n, 0.0015, 0.024)
    scuff = biquad_bandpass(rng.standard_normal(n), 1850.0, 2.0) * exp_env(n, 0.004, 0.028)
    place(out, plank * 0.68 + knock * 0.52 + scuff * 0.24, 0.0)
    return out * 0.9


def synth_step_sand(rng: np.random.Generator, variant: int) -> np.ndarray:
    """Granular desert sand / gravel crunch footstep."""
    dur = 0.26
    n = int(dur * SR)
    t = np.arange(n) / SR

    # Dense granular burst: high-frequency modulated noise grains
    grain_rate = 160.0 + variant * 25.0
    flutter = 0.55 + 0.45 * np.sin(2.0 * np.pi * grain_rate * t + rng.uniform(0, 6.28, n) * 0.3)
    noise = rng.standard_normal(n) * flutter

    crunch_hi = biquad_bandpass(noise, 3100.0 + variant * 180.0, 1.1) * exp_env(n, 0.012, 0.055)
    crunch_mid = biquad_bandpass(noise, 1250.0, 1.3) * exp_env(n, 0.008, 0.048)
    thud = np.sin(2.0 * np.pi * 78.0 * t * np.exp(-t * 16.0)) * exp_env(n, 0.004, 0.028)

    return (crunch_hi * 0.58 + crunch_mid * 0.48 + thud * 0.38) * 0.88


def synth_step_tile(rng: np.random.Generator, variant: int) -> np.ndarray:
    """Crisp polished marble / ceramic tile step."""
    dur = 0.22
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # Hard heel click + bright room reflection
    click = biquad_bandpass(rng.standard_normal(n), 3200.0 + variant * 220.0, 2.6) * exp_env(n, 0.001, 0.016)
    mid = biquad_bandpass(rng.standard_normal(n), 1150.0, 2.0) * exp_env(n, 0.0015, 0.022)
    thump = np.sin(2.0 * np.pi * 105.0 * t * np.exp(-t * 20.0)) * exp_env(n, 0.0015, 0.024)
    place(out, click * 0.62 + mid * 0.45 + thump * 0.50, 0.0)

    # Toe tap
    n2 = int(0.12 * SR)
    tap = biquad_bandpass(rng.standard_normal(n2), 2850.0 - variant * 150.0, 2.4) * exp_env(n2, 0.001, 0.014)
    place(out, tap * 0.38, 0.042)
    return out * 0.9


def synth_land(rng: np.random.Generator) -> np.ndarray:
    """Heavy two-boot landing impact with tactical gear rattle."""
    dur = 0.34
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # Sub-bass body impact
    sub = np.sin(2.0 * np.pi * 68.0 * t * np.exp(-t * 9.0)) * exp_env(n, 0.002, 0.065)
    # Boot 1 & Boot 2 staggered by 24 ms
    boot1 = biquad_bandpass(rng.standard_normal(n), 520.0, 1.0) * exp_env(n, 0.002, 0.045)
    boot2 = biquad_bandpass(rng.standard_normal(n), 680.0, 1.2) * exp_env(n, 0.002, 0.042)
    gear = biquad_bandpass(rng.standard_normal(n), 2600.0, 2.8) * exp_env(n, 0.008, 0.055)

    place(out, sub * 0.80 + boot1 * 0.65, 0.0)
    place(out, boot2 * 0.55 + gear * 0.35, 0.024)
    return out * 0.95


def synth_jump(rng: np.random.Generator) -> np.ndarray:
    """Quick upward boot push-off scuff and cloth rustle."""
    dur = 0.18
    n = int(dur * SR)
    t = np.arange(n) / SR
    scuff = biquad_bandpass(rng.standard_normal(n), 1150.0, 1.5) * exp_env(n, 0.003, 0.028)
    cloth = biquad_bandpass(rng.standard_normal(n), 2800.0, 1.8) * exp_env(n, 0.008, 0.036)
    push = np.sin(2.0 * np.pi * 95.0 * t * np.exp(-t * 18.0)) * exp_env(n, 0.002, 0.022)
    return (scuff * 0.55 + cloth * 0.35 + push * 0.45) * 0.85


# ---------------------------------------------------------------------------
# 2. Mechanical Weapon Reloads
# ---------------------------------------------------------------------------

def synth_reload_magout(rng: np.random.Generator) -> np.ndarray:
    """Magazine release catch click followed by polymer/metal magazine slide-out."""
    dur = 0.28
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)

    # 1. Metallic mag-catch button click at t = 0.005s
    n_click = int(0.06 * SR)
    t_c = np.arange(n_click) / SR
    click = (
        np.sin(2.0 * np.pi * 2450.0 * t_c) * exp_env(n_click, 0.0005, 0.008) * 0.6
        + biquad_bandpass(rng.standard_normal(n_click), 3400.0, 4.0) * exp_env(n_click, 0.0005, 0.007) * 0.7
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
    scrape = biquad_bandpass(rng.standard_normal(n_scr), 1950.0, 3.0) * exp_env(n_scr, 0.004, 0.020)
    place(out, scrape * 0.45, 0.0)

    # 2. Heavy receiver seating thud (t = 0.055s)
    n_thud = int(0.14 * SR)
    t_th = np.arange(n_thud) / SR
    thud_body = np.sin(2.0 * np.pi * 135.0 * t_th * np.exp(-t_th * 12.0)) * exp_env(n_thud, 0.001, 0.028)
    thud_mid = biquad_bandpass(rng.standard_normal(n_thud), 980.0, 2.2) * exp_env(n_thud, 0.001, 0.022)
    place(out, thud_body * 0.78 + thud_mid * 0.62, 0.055)

    # 3. Sharp steel mag-catch locking snap (t = 0.095s)
    n_lock = int(0.12 * SR)
    t_lk = np.arange(n_lock) / SR
    lock_ring = (
        np.sin(2.0 * np.pi * 2680.0 * t_lk) * exp_env(n_lock, 0.0005, 0.014) * 0.55
        + np.sin(2.0 * np.pi * 4120.0 * t_lk) * exp_env(n_lock, 0.0005, 0.010) * 0.38
    )
    lock_snap = biquad_bandpass(rng.standard_normal(n_lock), 3100.0, 3.8) * exp_env(n_lock, 0.0008, 0.016)
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
    pull_slide = biquad_bandpass(rng.standard_normal(n_pull), 2650.0, 3.5) * exp_env(n_pull, 0.008, 0.032)
    pull_ring = np.sin(2.0 * np.pi * 1880.0 * t_p) * exp_env(n_pull, 0.002, 0.020)
    place(out, pull_slide * 0.68 + pull_ring * 0.40, 0.0)

    # 2. Bolt release slam forward into battery (t = 0.115s) - unmistakable CS 1.6 metallic clack
    n_slam = int(0.20 * SR)
    t_s = np.arange(n_slam) / SR
    slam_impact = biquad_bandpass(rng.standard_normal(n_slam), 3350.0, 3.6) * exp_env(n_slam, 0.0008, 0.024)
    slam_low = np.sin(2.0 * np.pi * 210.0 * t_s * np.exp(-t_s * 16.0)) * exp_env(n_slam, 0.001, 0.022)
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
    "callout_reloading": "Cover me, reloading!",
    "callout_fire_in_the_hole": "Fire in the hole!",
    "callout_flashbang": "Flashbang out!",
    "callout_smoke_out": "Smoke out!",
    "callout_bomb_planted": "The bomb has been planted.",
    "callout_bomb_defused": "Counter-Terrorists win. Bomb defused.",
}


def apply_military_radio_dsp(voice_pcm: np.ndarray, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """Process clean speech waveform through an authentic CS 1.6 / tactical walkie-talkie chain."""
    # Trim leading/trailing silence
    abs_v = np.abs(voice_pcm)
    thresh = max(np.max(abs_v) * 0.03, 1e-4)
    nonzero = np.where(abs_v > thresh)[0]
    if len(nonzero) > 0:
        start = max(0, int(nonzero[0]) - int(0.015 * sr))
        end = min(len(voice_pcm), int(nonzero[-1]) + int(0.025 * sr))
        voice_pcm = voice_pcm[start:end]

    # Normalize speech input
    peak = np.max(np.abs(voice_pcm))
    if peak > 1e-5:
        voice_pcm = voice_pcm / peak

    # 1. Band-limit to walkie-talkie passband (340 Hz - 3100 Hz) + mid-presence boost at 1.6 kHz
    bp = biquad_highpass(voice_pcm, 340.0, 0.85, sr)
    bp = biquad_lowpass(bp, 3100.0, 0.90, sr)
    mid_presence = biquad_bandpass(bp, 1650.0, 1.4, sr)
    bp = bp * 0.75 + mid_presence * 0.55

    # 2. Walkie-talkie AGC compression + soft RF overdrive
    bp = np.tanh(bp * 2.4) * 0.82

    # 3. Frame with PTT key-on squelch (28 ms), speech, and PTT Roger beep + squelch tail (55 ms)
    pre_n = int(0.028 * sr)
    post_n = int(0.055 * sr)
    total_n = pre_n + len(bp) + post_n
    out = np.zeros(total_n, dtype=np.float64)

    # PTT key-on click & static burst
    t_pre = np.arange(pre_n) / sr
    ptt_on = (
        biquad_bandpass(rng.standard_normal(pre_n), 1900.0, 2.2, sr) * exp_env(pre_n, 0.001, 0.010, sr) * 0.45
        + np.sin(2.0 * np.pi * 1200.0 * t_pre) * exp_env(pre_n, 0.0005, 0.004, sr) * 0.25
    )
    out[:pre_n] = ptt_on

    # Main speech + subtle continuous radio carrier hiss
    carrier_hiss = biquad_bandpass(rng.standard_normal(len(bp)), 1800.0, 0.8, sr) * 0.028
    out[pre_n : pre_n + len(bp)] = bp + carrier_hiss

    # PTT release Roger chirp + squelch tail
    t_post = np.arange(post_n) / sr
    roger_beep = np.sin(2.0 * np.pi * 2150.0 * t_post) * np.where((t_post >= 0.004) & (t_post <= 0.022), 0.22, 0.0)
    squelch_tail = biquad_bandpass(rng.standard_normal(post_n), 2100.0, 1.5, sr) * exp_env(post_n, 0.002, 0.018, sr) * 0.35
    out[pre_n + len(bp) :] = roger_beep + squelch_tail

    return out


# Phoneme formant table: (F1, F2, F3, B1, B2, B3, voiced, fric_freq, fric_bw, fric_amp, nasal)
PHONEME_TABLE: dict[str, tuple[float, float, float, float, float, float, float, float, float, float, float]] = {
    # Vowels
    "AA": (730, 1090, 2440, 90, 110, 170, 1.0, 0, 0, 0.0, 0.0),
    "AE": (660, 1720, 2410, 85, 110, 170, 1.0, 0, 0, 0.0, 0.0),
    "AH": (620, 1220, 2550, 90, 110, 170, 1.0, 0, 0, 0.0, 0.0),
    "AO": (570, 840, 2410, 80, 100, 170, 1.0, 0, 0, 0.0, 0.0),
    "EH": (530, 1840, 2480, 80, 100, 170, 1.0, 0, 0, 0.0, 0.0),
    "ER": (490, 1350, 1690, 80, 100, 150, 1.0, 0, 0, 0.0, 0.0),
    "IH": (390, 1990, 2550, 70, 100, 170, 1.0, 0, 0, 0.0, 0.0),
    "IY": (270, 2290, 3010, 65, 95, 170, 1.0, 0, 0, 0.0, 0.0),
    "OW": (450, 880, 2450, 75, 100, 170, 1.0, 0, 0, 0.0, 0.0),
    "UH": (440, 1020, 2240, 75, 100, 170, 1.0, 0, 0, 0.0, 0.0),
    "UW": (300, 870, 2240, 70, 95, 170, 1.0, 0, 0, 0.0, 0.0),
    # Liquids / Glides
    "L":  (360, 1050, 2750, 75, 110, 180, 0.9, 0, 0, 0.0, 0.0),
    "R":  (420, 1300, 1650, 80, 110, 160, 0.9, 0, 0, 0.0, 0.0),
    "W":  (310, 750, 2200, 70, 100, 170, 0.9, 0, 0, 0.0, 0.0),
    "Y":  (280, 2150, 2900, 70, 100, 170, 0.9, 0, 0, 0.0, 0.0),
    # Nasals
    "M":  (280, 950, 2200, 90, 140, 200, 0.85, 0, 0, 0.0, 0.6),
    "N":  (300, 1450, 2450, 90, 140, 200, 0.85, 0, 0, 0.0, 0.6),
    "NG": (290, 1950, 2600, 95, 150, 210, 0.85, 0, 0, 0.0, 0.65),
    # Fricatives & Affricates
    "S":  (350, 1600, 2700, 150, 200, 250, 0.0, 4200, 900, 0.65, 0.0),
    "SH": (380, 1800, 2500, 150, 200, 250, 0.0, 2650, 700, 0.70, 0.0),
    "F":  (380, 1250, 2400, 150, 200, 250, 0.0, 1950, 1100, 0.42, 0.0),
    "TH": (380, 1450, 2500, 150, 200, 250, 0.0, 2400, 1200, 0.35, 0.0),
    "HH": (500, 1500, 2500, 180, 220, 260, 0.15, 1600, 900, 0.48, 0.0),
    "Z":  (340, 1600, 2700, 100, 150, 200, 0.65, 4000, 900, 0.45, 0.0),
    "V":  (340, 1200, 2350, 100, 150, 200, 0.65, 1800, 950, 0.32, 0.0),
    "DH": (350, 1450, 2500, 100, 150, 200, 0.70, 2100, 1000, 0.28, 0.0),
    "CH": (360, 1800, 2550, 140, 180, 230, 0.0, 2800, 650, 0.80, 0.0),
    "JH": (340, 1800, 2550, 110, 160, 210, 0.60, 2700, 650, 0.62, 0.0),
    # Stops / Plosives
    "P":  (320, 1100, 2300, 130, 180, 220, 0.0, 1100, 800, 0.55, 0.0),
    "T":  (350, 1750, 2650, 130, 180, 220, 0.0, 3400, 950, 0.70, 0.0),
    "K":  (360, 1450, 2350, 130, 180, 220, 0.0, 1950, 750, 0.68, 0.0),
    "B":  (260, 1050, 2250, 90, 130, 180, 0.55, 950, 650, 0.38, 0.0),
    "D":  (280, 1700, 2600, 90, 130, 180, 0.55, 2800, 850, 0.45, 0.0),
    "G":  (280, 1450, 2300, 90, 130, 180, 0.55, 1750, 700, 0.45, 0.0),
    # Pause
    "_":  (400, 1400, 2400, 200, 250, 300, 0.0, 0, 0, 0.0, 0.0),
}

# Phonetic transcriptions: list of (phoneme, duration_s, pitch_hz)
CALLOUT_SCORE: dict[str, list[tuple[str, float, float]]] = {
    "callout_spotted": [
        ("EH", 0.085, 138), ("N", 0.055, 134), ("AH", 0.065, 130), ("M", 0.065, 128), ("IY", 0.095, 132),
        ("_", 0.035, 125),
        ("S", 0.075, 140), ("P", 0.045, 142), ("AA", 0.130, 148), ("T", 0.050, 136), ("IH", 0.075, 124), ("D", 0.065, 112),
    ],
    "callout_watch": [
        ("HH", 0.055, 132), ("OW", 0.115, 136), ("L", 0.065, 130), ("D", 0.045, 126), ("IH", 0.065, 124), ("NG", 0.070, 122),
        ("_", 0.030, 120),
        ("DH", 0.045, 125), ("IH", 0.075, 128), ("S", 0.065, 126),
        ("_", 0.030, 124),
        ("AE", 0.120, 138), ("NG", 0.065, 130), ("G", 0.045, 122), ("AH", 0.065, 116), ("L", 0.085, 110),
    ],
    "callout_danger": [
        ("D", 0.055, 152), ("EY", 0.110, 158) if "EY" in PHONEME_TABLE else ("EH", 0.070, 158), ("IY", 0.055, 162),
        ("N", 0.060, 150), ("JH", 0.065, 142), ("ER", 0.125, 132),
        ("_", 0.055, 125),
        ("F", 0.065, 146), ("AO", 0.115, 150), ("L", 0.070, 138),
        ("_", 0.030, 134),
        ("B", 0.050, 144), ("AE", 0.130, 140), ("K", 0.065, 118),
    ],
    "callout_utility": [
        ("N", 0.060, 134), ("IY", 0.110, 142), ("D", 0.050, 132),
        ("_", 0.025, 128),
        ("Y", 0.050, 130), ("UW", 0.075, 134), ("T", 0.050, 140), ("IH", 0.085, 146),
        ("L", 0.055, 136), ("IH", 0.060, 128), ("T", 0.045, 124), ("IY", 0.095, 122),
        ("_", 0.025, 120),
        ("HH", 0.050, 126), ("IH", 0.085, 122), ("R", 0.105, 112),
    ],
    "callout_reloading": [
        ("K", 0.055, 144), ("AH", 0.095, 150), ("V", 0.055, 140), ("ER", 0.095, 132),
        ("_", 0.025, 128),
        ("M", 0.065, 134), ("IY", 0.105, 128),
        ("_", 0.045, 124),
        ("R", 0.060, 136), ("IY", 0.085, 142), ("L", 0.065, 146), ("OW", 0.125, 140),
        ("D", 0.050, 128), ("IH", 0.070, 120), ("NG", 0.095, 112),
    ],
    "callout_fire_in_the_hole": [
        ("F", 0.070, 154), ("AA", 0.095, 162), ("IH", 0.060, 158), ("ER", 0.085, 148),
        ("_", 0.025, 142),
        ("IH", 0.060, 144), ("N", 0.050, 138),
        ("_", 0.020, 134),
        ("DH", 0.040, 132), ("AH", 0.055, 130),
        ("_", 0.025, 128),
        ("HH", 0.060, 148), ("OW", 0.145, 142), ("L", 0.105, 116),
    ],
    "callout_flashbang": [
        ("F", 0.065, 146), ("L", 0.055, 148), ("AE", 0.115, 154), ("SH", 0.085, 144),
        ("B", 0.050, 138), ("AE", 0.110, 142), ("NG", 0.075, 132),
        ("_", 0.030, 128),
        ("AA", 0.095, 136), ("UH", 0.065, 124), ("T", 0.065, 114),
    ],
    "callout_smoke_out": [
        ("S", 0.080, 136), ("M", 0.065, 138), ("OW", 0.130, 144), ("K", 0.055, 132),
        ("_", 0.035, 128),
        ("AA", 0.105, 138), ("UH", 0.070, 124), ("T", 0.065, 112),
    ],
    "callout_bomb_planted": [
        ("B", 0.055, 122), ("AA", 0.135, 126), ("M", 0.080, 120),
        ("_", 0.040, 118),
        ("HH", 0.045, 120), ("AE", 0.075, 122), ("Z", 0.055, 118),
        ("_", 0.025, 116),
        ("B", 0.045, 118), ("IH", 0.065, 120), ("N", 0.055, 118),
        ("_", 0.030, 116),
        ("P", 0.055, 124), ("L", 0.055, 126), ("AE", 0.120, 128), ("N", 0.060, 120),
        ("T", 0.050, 114), ("IH", 0.070, 108), ("D", 0.075, 102),
    ],
    "callout_bomb_defused": [
        ("B", 0.055, 126), ("AA", 0.130, 132), ("M", 0.075, 126),
        ("_", 0.045, 122),
        ("D", 0.050, 128), ("IY", 0.085, 134), ("F", 0.070, 138), ("Y", 0.050, 142),
        ("UW", 0.135, 136), ("Z", 0.075, 118), ("D", 0.070, 108),
    ],
}


def time_varying_resonator(x: np.ndarray, f_arr: np.ndarray, bw_arr: np.ndarray, sr: int = SR) -> np.ndarray:
    """2nd-order digital formant resonator with sample-by-sample interpolated center frequency and bandwidth."""
    r = np.exp(-np.pi * np.clip(bw_arr, 30.0, 2000.0) / sr)
    theta = 2.0 * np.pi * np.clip(f_arr, 80.0, sr * 0.46) / sr
    a1 = -2.0 * r * np.cos(theta)
    a2 = r * r
    b0 = 1.0 - r * np.cos(theta)

    y = np.zeros_like(x, dtype=np.float64)
    y1 = 0.0
    y2 = 0.0
    for i in range(len(x)):
        yi = b0[i] * float(x[i]) - a1[i] * y1 - a2[i] * y2
        y2 = y1
        y1 = yi
        y[i] = yi
    return y


def synth_klatt_callout(score: list[tuple[str, float, float]], rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """Synthesize articulated tactical speech from a phoneme score using a Klatt cascade/parallel formant model."""
    total_dur = sum(d for _, d, _ in score)
    total_n = int(round(total_dur * sr))

    f0_tr = np.zeros(total_n)
    f1_tr = np.zeros(total_n)
    f2_tr = np.zeros(total_n)
    f3_tr = np.zeros(total_n)
    b1_tr = np.zeros(total_n)
    b2_tr = np.zeros(total_n)
    b3_tr = np.zeros(total_n)
    voicing_tr = np.zeros(total_n)
    fric_f_tr = np.zeros(total_n)
    fric_b_tr = np.zeros(total_n)
    fric_a_tr = np.zeros(total_n)
    plosive_burst = np.zeros(total_n)

    cursor = 0
    for ph, dur, pitch in score:
        n_seg = min(int(round(dur * sr)), total_n - cursor)
        if n_seg <= 0:
            break
        f1, f2, f3, b1, b2, b3, voiced, ff, fb, fa, nasal = PHONEME_TABLE.get(ph, PHONEME_TABLE["AH"])
        sl = slice(cursor, cursor + n_seg)
        f0_tr[sl] = pitch
        f1_tr[sl] = f1
        f2_tr[sl] = f2
        f3_tr[sl] = f3
        b1_tr[sl] = b1 * (1.25 if nasal > 0 else 1.0)
        b2_tr[sl] = b2
        b3_tr[sl] = b3
        voicing_tr[sl] = voiced
        fric_f_tr[sl] = max(ff, 500.0)
        fric_b_tr[sl] = max(fb, 300.0)
        fric_a_tr[sl] = fa

        # Articulated syllable envelope + stop-consonant release burst
        seg_env = np.sin(np.pi * np.linspace(0.05, 0.95, n_seg)) ** 0.35
        voicing_tr[sl] *= seg_env
        if ph in ("P", "T", "K", "B", "D", "G", "CH", "JH"):
            # Silent occlusion during first 40% followed by crisp plosive burst
            occ = int(n_seg * 0.42)
            voicing_tr[cursor : cursor + occ] *= 0.12
            fric_a_tr[cursor : cursor + occ] = 0.0
            burst_len = min(int(0.018 * sr), n_seg - occ)
            if burst_len > 0:
                plosive_burst[cursor + occ : cursor + occ + burst_len] = (
                    rng.standard_normal(burst_len) * exp_env(burst_len, 0.001, 0.006, sr) * fa * 1.4
                )
        cursor += n_seg

    # Smoothly co-articulate formant trajectories (18 ms moving-average kernel)
    k_len = max(3, int(0.018 * sr))
    kernel = np.hanning(k_len)
    kernel /= np.sum(kernel)
    for arr in (f0_tr, f1_tr, f2_tr, f3_tr, b1_tr, b2_tr, b3_tr, voicing_tr, fric_f_tr, fric_b_tr, fric_a_tr):
        arr[:] = np.convolve(arr, kernel, mode="same")

    # Natural laryngeal micro-jitter and shimmer
    t = np.arange(total_n) / sr
    vibrato = 1.0 + 0.012 * np.sin(2.0 * np.pi * 5.8 * t) + 0.006 * biquad_lowpass(rng.standard_normal(total_n), 18.0, 0.7, sr)
    phase = np.cumsum(f0_tr * vibrato / sr) % 1.0

    # Rosenberg glottal pulse + first-difference lip radiation
    glottal = np.where(
        phase < 0.42,
        0.5 * (1.0 - np.cos(np.pi * phase / 0.42)),
        np.where(phase < 0.58, np.cos(0.5 * np.pi * (phase - 0.42) / 0.16), 0.0),
    )
    glottal_diff = np.diff(glottal, prepend=glottal[0]) * sr * 0.00025
    aspiration = rng.standard_normal(total_n) * 0.08 * (0.3 + 0.7 * voicing_tr)
    source = glottal_diff * voicing_tr + aspiration

    # 3-formant vocal tract cascade + fixed F4 head resonance (3450 Hz)
    y = time_varying_resonator(source, f1_tr, b1_tr, sr)
    y = time_varying_resonator(y, f2_tr, b2_tr, sr) * 0.65
    y = time_varying_resonator(y, f3_tr, b3_tr, sr) * 0.55
    y = biquad_bandpass(y, 3300.0, 3.5, sr) * 0.25 + y * 0.85

    # Parallel fricative + plosive consonant branch
    fric_src = rng.standard_normal(total_n) * fric_a_tr + plosive_burst
    fric_out = time_varying_resonator(fric_src, fric_f_tr, fric_b_tr, sr) * 0.45

    return y + fric_out


def generate_callouts(out_dir: Path, rng: np.random.Generator) -> None:
    """Synthesize spoken tactical radio callouts offline via Klatt formant synthesis + walkie-talkie DSP."""
    for name, text in CALLOUT_LINES.items():
        score = CALLOUT_SCORE[name]
        raw_voice = synth_klatt_callout(score, rng, SR)
        processed = apply_military_radio_dsp(raw_voice, rng, SR)
        p = out_dir / f"{name}.wav"
        write_wav(p, processed, SR)
        print(f"  [wav] {name:26s} ({len(processed)/SR:.2f}s, {p.stat().st_size // 1024:2d} KB) -> '{text}'")


def main() -> None:
    here = Path(__file__).resolve().parent
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else here.parent / "apps" / "web" / "public" / "sounds" / "hassault"
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(42)

    print(f"Generating HorribleAssault .wav sound suite in {out_dir} ...")

    # 1. Multi-surface walking footsteps & movement
    step_files = {
        "step_concrete_1": synth_step_concrete(rng, 1),
        "step_concrete_2": synth_step_concrete(rng, 2),
        "step_metal_1": synth_step_metal(rng, 1),
        "step_metal_2": synth_step_metal(rng, 2),
        "step_wood_1": synth_step_wood(rng, 1),
        "step_wood_2": synth_step_wood(rng, 2),
        "step_sand_1": synth_step_sand(rng, 1),
        "step_sand_2": synth_step_sand(rng, 2),
        "step_tile_1": synth_step_tile(rng, 1),
        "step_tile_2": synth_step_tile(rng, 2),
        "land": synth_land(rng),
        "jump": synth_jump(rng),
    }
    for name, data in step_files.items():
        p = out_dir / f"{name}.wav"
        write_wav(p, data, SR)
        print(f"  [wav] {name:26s} ({len(data)/SR:.2f}s, {p.stat().st_size // 1024:2d} KB)")

    # 2. Mechanical weapon reloads
    reload_files = {
        "reload_magout": synth_reload_magout(rng),
        "reload_magin": synth_reload_magin(rng),
        "reload_bolt": synth_reload_bolt(rng),
        "reload": synth_reload_full(rng),
    }
    for name, data in reload_files.items():
        p = out_dir / f"{name}.wav"
        write_wav(p, data, SR)
        print(f"  [wav] {name:26s} ({len(data)/SR:.2f}s, {p.stat().st_size // 1024:2d} KB)")

    # 3. Tactical radio player callouts
    generate_callouts(out_dir, rng)
    print("Done.")


if __name__ == "__main__":
    main()

