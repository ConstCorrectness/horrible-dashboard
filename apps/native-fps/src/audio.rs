//! The game's sound, synthesized from nothing — the Rust half of `audio.ts`.
//!
//! There are no audio files here and there will not be. AssaultCube's sounds are
//! copyright, the same restriction that keeps its maps and textures out of this
//! repo, so every noise is generated: a footstep is a filtered noise burst with
//! an envelope, a shot is a louder one with a thump under it.
//!
//! That constraint suits the mechanic. What the server sends is a **bearing and
//! a loudness** (`backend/modules/hassault/noise.py`) — never a position, because
//! broadcasting footstep positions and letting clients decide what is audible is
//! a wall hack made of sound. A bearing and a loudness is exactly pan and gain,
//! so there is nothing to look up and nothing to load: the first footstep of a
//! match is as instant as the last.
//!
//! ### How it reaches the speakers
//!
//! One cpal output stream, and **the audio thread owns every voice**. The game
//! thread never touches them: it sends a `Voice` down an `mpsc` channel, and the
//! callback drains the channel before mixing. A `Mutex<Vec<Voice>>` would have
//! been shorter and is the classic way to get an audible glitch — the audio
//! callback runs on a real-time thread, and blocking it on a lock the game thread
//! holds is a dropout, not a delay.
//!
//! Everything is mixed by hand because there is nothing to mix *with*: no graph,
//! no nodes, just a band-passed noise burst and an optional sine body summed into
//! the output buffer. Roughly what WebAudio is doing for the browser client, at
//! the scale this game needs (twelve simultaneous voices).

use std::sync::mpsc::{Receiver, SyncSender, TrySendError};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

use crate::api::WeaponSpec;
use crate::protocol::NoiseEvent;

/// A noise that would be inaudible is not worth a voice. The browser's floor.
const MIN_GAIN: f32 = 0.02;

/// Voices allowed to overlap.
///
/// A firefight between eight players can produce a lot of noise in one 50 ms
/// tick. Past this the newest are dropped, which is also roughly what ears do.
const MAX_VOICES: usize = 12;

/// How many voices can be queued between two audio callbacks.
///
/// Bounded on purpose: `try_send` on a full channel **drops the sound** rather
/// than blocking the game thread. A frame loop that waits on the audio thread is
/// a frame loop with the audio thread's scheduling jitter in it.
const QUEUE: usize = 64;

/// One kind of sound. The browser's `Timbre`, field for field.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Timbre {
    /// Centre of the band-pass, in Hz. Low is a thump, high is a snap.
    pub frequency: f32,
    /// How wide the band is. A narrow band rings, a wide one hisses.
    pub q: f32,
    /// Seconds of decay.
    pub decay: f32,
    /// Relative loudness, before distance falloff.
    pub gain: f32,
    /// A short sine thump under the noise, in Hz — 0 for none.
    pub body: f32,
}

/// One timbre per noise kind, mirroring `audio.ts`.
///
/// Chosen so the *kind* is identifiable without looking: a footstep is short and
/// mid-band, a landing has a low thump under it, death is the longest sound in
/// the game. Telling a reload from a footstep behind you is real information, so
/// they are deliberately far apart.
pub fn timbre(kind: &str) -> Timbre {
    match kind {
        "land" => Timbre {
            frequency: 380.0,
            q: 0.9,
            decay: 0.22,
            gain: 0.85,
            body: 62.0,
        },
        "jump" => Timbre {
            frequency: 900.0,
            q: 1.6,
            decay: 0.09,
            gain: 0.35,
            body: 0.0,
        },
        "shot" => Timbre {
            frequency: 1500.0,
            q: 0.7,
            decay: 0.3,
            gain: 1.0,
            body: 78.0,
        },
        "reload" => Timbre {
            frequency: 2400.0,
            q: 3.4,
            decay: 0.14,
            gain: 0.4,
            body: 0.0,
        },
        // An item leaving the map. Bright, short and unmistakably not a gun: the
        // sound has to be legible as "somebody took the armour" from across a
        // room, where a dull one would be mistaken for a footstep. The browser's
        // `pickup`, to the digit.
        "pickup" => Timbre {
            frequency: 1650.0,
            q: 5.5,
            decay: 0.16,
            gain: 0.45,
            body: 0.0,
        },
        // Breaking the surface. Low Q so the burst stays broadband — a splash is
        // noise, and a narrow filter turns it into a bell.
        "splash" => Timbre {
            frequency: 900.0,
            q: 0.6,
            decay: 0.34,
            gain: 0.75,
            body: 40.0,
        },
        "hurt" => Timbre {
            frequency: 300.0,
            q: 2.2,
            decay: 0.3,
            gain: 0.7,
            body: 120.0,
        },
        "die" => Timbre {
            frequency: 220.0,
            q: 1.4,
            decay: 0.6,
            gain: 0.9,
            body: 55.0,
        },
        // A grenade leaving the hand: a short soft rush, quieter than a footstep
        // so it is a cue for the thrower rather than an announcement to the room.
        "throw" => Timbre {
            frequency: 1100.0,
            q: 1.2,
            decay: 0.12,
            gain: 0.3,
            body: 0.0,
        },
        // The four detonations, and they have to be tellable apart with your
        // back turned — which is most of what a grenade is: information.
        //
        // **Low and long is an explosion; high and short is a bang.** The HE
        // gets the longest decay and the most body in the room, the flash is a
        // crack with no weight behind it, the smoke is a hiss, and fire is a low
        // roar. Without these rows every detonation fell through to the footstep
        // fallback below and an HE going off at your feet sounded like somebody
        // walking past.
        "explosion" => Timbre {
            frequency: 90.0,
            q: 0.5,
            decay: 0.9,
            gain: 1.0,
            body: 150.0,
        },
        "nade_flash" => Timbre {
            frequency: 2600.0,
            q: 0.6,
            decay: 0.35,
            gain: 1.0,
            body: 30.0,
        },
        "nade_smoke" => Timbre {
            frequency: 4200.0,
            q: 0.35,
            decay: 0.85,
            gain: 0.5,
            body: 0.0,
        },
        "nade_fire" => Timbre {
            frequency: 210.0,
            q: 0.45,
            decay: 1.1,
            gain: 0.7,
            body: 70.0,
        },
        // `step`, and the fallback for a kind this build does not know: a noise
        // the server invented later must still be *audible*, because the whole
        // mechanic is hearing that something happened.
        _ => Timbre {
            frequency: 620.0,
            q: 1.1,
            decay: 0.1,
            gain: 0.5,
            body: 90.0,
        },
    }
}

/// A weapon's own voice, **derived from its balance numbers** rather than given a
/// table of its own.
///
/// The same reasoning as `shot_loudness` scaling with damage: tie the sound to
/// numbers the weapon already has and a balance change cannot leave the audio
/// describing the previous version of the gun. A weapon added to `weapons.py`
/// then arrives with a voice and no client release.
///
/// - **`rpm` picks the pitch** — a fast, small weapon cracks and a slow, heavy
///   one booms, which is the cue that separates a rifle from a sniper at range.
/// - **`pellets` widens the band** — more than one is a shotgun, and a wide band
///   is a blast rather than a bang.
/// - **`damage` sets the body and the decay** — weight, and how long it hangs.
///
/// This is deliberately the same arithmetic as `audio.ts`'s `weaponVoice`, and
/// deliberately duplicated rather than served: unlike `interval` or `zoomLevels`,
/// nothing here is a number the client *acts* on. A drift makes a gun sound
/// wrong, it does not make a shot land somewhere else.
pub fn weapon_voice(weapon: Option<&WeaponSpec>) -> Timbre {
    let Some(weapon) = weapon else {
        return timbre("shot");
    };
    // The knife, told apart exactly as `shot_loudness` tells it. A swing has to
    // *not* sound like a gun: being able to hear that somebody is carrying a
    // knife is the reason its silence is worth anything.
    if weapon.kickback <= 0.0 && weapon.range <= 6.0 {
        return Timbre {
            frequency: 2600.0,
            q: 2.2,
            decay: 0.12,
            gain: 0.45,
            body: 0.0,
        };
    }
    let fast = (weapon.rpm / 700.0).min(1.0);
    let heft = (weapon.damage / 90.0).min(1.0);
    Timbre {
        frequency: 900.0 + fast * 1100.0,
        q: if weapon.pellets > 1 { 0.35 } else { 0.75 },
        decay: 0.16 + heft * 0.2,
        gain: 1.0,
        body: 95.0 - heft * 42.0,
    }
}

/// One sound being played, owned by the audio thread from the moment it is sent.
struct Voice {
    timbre: Timbre,
    gain: f32,
    /// Constant-power stereo gains, precomputed on the game thread so the audio
    /// callback runs no trigonometry per voice.
    left: f32,
    right: f32,
    /// Seconds elapsed.
    t: f32,
    /// Chamberlin state-variable filter state.
    low: f32,
    band: f32,
    /// The body oscillator's phase.
    phase: f32,
    rng: u32,
}

impl Voice {
    /// One sample, as `(left, right)`.
    fn sample(&mut self, dt: f32) -> (f32, f32) {
        self.t += dt;
        // A 4 ms linear attack into an exponential decay — the browser's
        // envelope, which is what stops every sound starting with a click.
        let env = if self.t < 0.004 {
            self.t / 0.004
        } else {
            let k = (self.t - 0.004) / self.timbre.decay.max(1e-4);
            (-6.0 * k).exp()
        };

        // White noise through a band-pass. The filter is two integrators
        // (Chamberlin's SVF) rather than a biquad: same shape, and its
        // coefficients are `sin` and a reciprocal rather than a design step.
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 17;
        self.rng ^= self.rng << 5;
        let white = (self.rng >> 8) as f32 / (1u32 << 23) as f32 - 1.0;
        // The SVF's two coefficients, and **its stability condition is a
        // relationship between them**, not a bound on each: it holds while
        // `f < 2 - q`, so a bright centre frequency and a wide band are only
        // unstable *together*. Clamping them independently is what the first
        // version did, and the failure is not a quiet distortion — it is
        // full-scale noise, at the exact moment a shotgun goes off.
        let q = (1.0 / self.timbre.q.max(0.05)).min(1.5);
        let f = (2.0 * std::f32::consts::PI * self.timbre.frequency * dt)
            .sin()
            .min((2.0 - q) * 0.9);
        self.low += f * self.band;
        let high = white - self.low - q * self.band;
        self.band += f * high;
        let mut out = self.band * env * self.gain;

        if self.timbre.body > 0.0 {
            // A sine thump under the noise, dropping in pitch. What makes a
            // landing feel like weight arriving rather than a hiss.
            let hz = self.timbre.body * (1.0 - 0.4 * (self.t / self.timbre.decay).min(1.0));
            self.phase += hz * dt;
            if self.phase > 1.0 {
                self.phase -= 1.0;
            }
            out += (self.phase * std::f32::consts::TAU).sin() * env * self.gain * 0.7;
        }
        (out * self.left, out * self.right)
    }

    fn done(&self) -> bool {
        self.t > self.timbre.decay + 0.05
    }
}

/// The game's handle on the audio device.
pub struct GameAudio {
    tx: SyncSender<Voice>,
    volume: f32,
    /// Kept alive: dropping a cpal stream stops it. It is deliberately not sent
    /// anywhere — cpal's `Stream` is not `Send` on every platform, which is why
    /// the voices travel and the stream does not.
    _stream: cpal::Stream,
}

impl GameAudio {
    /// Open the default output device.
    ///
    /// `None` when there is no device or it refuses a config — a machine with no
    /// sound card, a headless CI box, a Linux session with no audio server. The
    /// caller plays on without sound rather than failing to start: this client
    /// is a *game*, and refusing to run because a speaker is missing would be the
    /// wrong trade.
    pub fn open() -> Option<GameAudio> {
        let device = cpal::default_host().default_output_device()?;
        let config = device.default_output_config().ok()?;
        let sample_rate = config.sample_rate() as f32;
        let channels = config.channels() as usize;
        let dt = 1.0 / sample_rate;

        let (tx, rx): (SyncSender<Voice>, Receiver<Voice>) = std::sync::mpsc::sync_channel(QUEUE);
        let mut voices: Vec<Voice> = Vec::with_capacity(MAX_VOICES);

        let stream = device
            .build_output_stream(
                config.config(),
                move |out: &mut [f32], _: &cpal::OutputCallbackInfo| {
                    // Drained here, not inside the sample loop: every voice this
                    // buffer will play is admitted once, up front.
                    while let Ok(voice) = rx.try_recv() {
                        if voices.len() < MAX_VOICES {
                            voices.push(voice);
                        }
                    }
                    for frame in out.chunks_mut(channels) {
                        let (mut l, mut r) = (0.0f32, 0.0f32);
                        for voice in voices.iter_mut() {
                            let (vl, vr) = voice.sample(dt);
                            l += vl;
                            r += vr;
                        }
                        // Clipped rather than normalised: a limiter that ducked
                        // the whole mix when a grenade went off would make every
                        // other cue quieter exactly when they matter most.
                        let (l, r) = (l.clamp(-1.0, 1.0), r.clamp(-1.0, 1.0));
                        for (i, sample) in frame.iter_mut().enumerate() {
                            // Mono devices get the left channel; anything past
                            // the second gets the right, which is wrong for 5.1
                            // and inaudibly so.
                            *sample = if i == 0 { l } else { r };
                        }
                    }
                    voices.retain(|v| !v.done());
                },
                |err| eprintln!("hassault: audio device error: {err}"),
                None,
            )
            .ok()?;
        stream.play().ok()?;
        Some(GameAudio {
            tx,
            volume: 1.0,
            _stream: stream,
        })
    }

    /// 0 silences it entirely.
    pub fn set_volume(&mut self, volume: f32) {
        self.volume = volume.clamp(0.0, 1.0);
    }

    /// Play one noise.
    ///
    /// `listener_yaw` rotates the world bearing into the listener's own frame,
    /// which is what makes the pan mean anything: a sound on your left has to
    /// move to your right when you turn around.
    pub fn play(
        &self,
        kind: &str,
        volume: f32,
        bearing: f32,
        listener_yaw: f32,
        up: i32,
        voice: Option<Timbre>,
    ) {
        let volume = volume * self.volume;
        if volume < MIN_GAIN {
            return;
        }
        let mut timbre = voice.unwrap_or_else(|| self::timbre(kind));
        // Sounds from above and below are *tilted*, not positioned: stereo has no
        // elevation, and a filter tilt is the cue headphones actually convey.
        timbre.frequency *= match up {
            i32::MIN..=-1 => 0.8,
            0 => 1.0,
            _ => 1.25,
        };
        // The sine of the bearing relative to the listener is the left/right
        // component, and all a stereo pan can carry.
        let pan = (bearing - listener_yaw).sin().clamp(-1.0, 1.0);
        let voice = Voice {
            timbre,
            gain: volume * timbre.gain,
            // Constant power, like WebAudio's stereo panner: a sound panned hard
            // left must not be quieter than the same sound centred.
            left: ((1.0 - pan) * 0.5).sqrt(),
            right: ((1.0 + pan) * 0.5).sqrt(),
            t: 0.0,
            low: 0.0,
            band: 0.0,
            phase: 0.0,
            // Seeded per voice so two footsteps are never the same waveform,
            // which is audible immediately — as a machine gun rather than a
            // person walking.
            rng: 0x2545_f491 ^ (volume.to_bits().rotate_left(7)) ^ timbre.frequency.to_bits(),
        };
        match self.tx.try_send(voice) {
            Ok(()) => {}
            // A full queue is a tick that made more noise than the audio thread
            // has woken up for. Dropping the newest is what MAX_VOICES does
            // anyway, one buffer later.
            Err(TrySendError::Full(_)) => {}
            Err(TrySendError::Disconnected(_)) => {}
        }
    }

    /// Play a noise the server sent us.
    ///
    /// `weapons` is the served loadout, so a shot's `weapon` id becomes that
    /// gun's voice. An unrecognised weapon still plays, in the generic shot
    /// voice — the alternative is a gunshot you cannot hear because your client
    /// is older than the server.
    pub fn heard(&self, event: &NoiseEvent, listener_yaw: f32, weapons: &[WeaponSpec]) {
        let voice = if event.kind == "shot" && !event.weapon.is_empty() {
            Some(weapon_voice(weapons.iter().find(|w| w.id == event.weapon)))
        } else {
            None
        };
        self.play(
            &event.kind,
            event.volume,
            event.bearing,
            listener_yaw,
            event.up,
            voice,
        );
    }

    /// Play one of *our own* noises, at full volume and dead centre.
    ///
    /// The server deliberately does not send these back: they need no round trip,
    /// and a footstep that arrives 50 ms after the step does not sound like a
    /// footstep.
    pub fn own(&self, kind: &str, volume: f32, weapon: Option<&WeaponSpec>) {
        let voice = weapon.map(|w| weapon_voice(Some(w)));
        self.play(kind, volume, 0.0, 0.0, 0, voice);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(id: &str) -> WeaponSpec {
        WeaponSpec {
            id: id.into(),
            name: id.into(),
            damage: 30.0,
            rpm: 700.0,
            pellets: 1,
            range: 200.0,
            kickback: 4.0,
            ..Default::default()
        }
    }

    #[test]
    fn a_slow_heavy_weapon_booms_and_a_fast_one_cracks() {
        // The cue that separates a rifle from a sniper across a map, and it is
        // read off `rpm` so a balance change moves the sound with it.
        let mut sniper = spec("sniper");
        sniper.rpm = 62.0;
        sniper.damage = 90.0;
        let assault = weapon_voice(Some(&spec("assault")));
        let sniper = weapon_voice(Some(&sniper));
        assert!(sniper.frequency < assault.frequency);
        assert!(sniper.decay > assault.decay);
        assert!(sniper.body < assault.body);
    }

    #[test]
    fn pellets_widen_the_band() {
        let mut shotgun = spec("shotgun");
        shotgun.pellets = 8;
        assert!(weapon_voice(Some(&shotgun)).q < weapon_voice(Some(&spec("assault"))).q);
    }

    #[test]
    fn the_knife_does_not_sound_like_a_gun() {
        // Hearing that somebody is carrying a knife is the reason its silence is
        // worth anything, so it has to be unmistakably not a gunshot rather than
        // a quieter one.
        let mut knife = spec("knife");
        knife.kickback = 0.0;
        knife.range = 5.0;
        let voice = weapon_voice(Some(&knife));
        assert_eq!(voice.body, 0.0);
        assert!(voice.gain < weapon_voice(Some(&spec("assault"))).gain);
    }

    #[test]
    fn an_unknown_weapon_is_still_audible() {
        // A client older than the server must still hear a gunshot.
        assert_eq!(weapon_voice(None), timbre("shot"));
        assert!(timbre("something-new").gain > 0.0);
    }

    #[test]
    fn a_voice_decays_to_silence_and_then_retires() {
        let mut voice = Voice {
            timbre: timbre("shot"),
            gain: 1.0,
            left: 1.0,
            right: 1.0,
            t: 0.0,
            low: 0.0,
            band: 0.0,
            phase: 0.0,
            rng: 12345,
        };
        let dt = 1.0 / 48_000.0;
        let mut peak_early: f32 = 0.0;
        let mut peak_late: f32 = 0.0;
        for i in 0..24_000 {
            let (l, _) = voice.sample(dt);
            if i < 480 {
                peak_early = peak_early.max(l.abs());
            } else if i > 20_000 {
                peak_late = peak_late.max(l.abs());
            }
            // The stability check that matters: an SVF driven past its limit does
            // not distort quietly, it produces full-scale noise.
            assert!(l.is_finite() && l.abs() < 4.0, "sample {i} was {l}");
        }
        assert!(peak_early > peak_late * 10.0, "{peak_early} vs {peak_late}");
        assert!(voice.done());
    }

    #[test]
    fn the_filter_stays_stable_at_the_brightest_timbre() {
        // The knife's 2600 Hz against a low sample rate is the worst case in the
        // game: the SVF's coefficient must be clamped before it reaches 2.
        let mut voice = Voice {
            timbre: Timbre {
                frequency: 2600.0,
                q: 0.05,
                decay: 0.2,
                gain: 1.0,
                body: 0.0,
            },
            gain: 1.0,
            left: 1.0,
            right: 1.0,
            t: 0.0,
            low: 0.0,
            band: 0.0,
            phase: 0.0,
            rng: 99,
        };
        let dt = 1.0 / 8_000.0;
        for _ in 0..8_000 {
            let (l, r) = voice.sample(dt);
            assert!(l.is_finite() && r.is_finite());
            assert!(l.abs() < 8.0, "{l}");
        }
    }
}
