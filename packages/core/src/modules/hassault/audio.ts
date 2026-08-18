/**
 * The game's sound: synthesized from nothing, every time.
 *
 * There are no audio files here and there will not be. AssaultCube's sounds are
 * copyright — the same restriction that keeps its maps and textures out of this
 * repo — so this game's audio is generated, exactly as its maps are painted from
 * JSON brushes rather than shipped as `.cgz` blobs. A footstep is a filtered noise
 * burst with an envelope; a shot is a louder one with a click on the front. None of
 * it is anybody else's work.
 *
 * That constraint turns out to suit the mechanic. What the server sends is a
 * bearing and a loudness (see `backend/modules/hassault/noise.py`), which is
 * precisely what this needs: pan and gain. There is nothing to look up and nothing
 * to load, so the first footstep of a match is as instant as the last.
 *
 * Kept out of the render loop's hot path in the only way that matters — an
 * `AudioContext` is created lazily on the first sound and reused, because
 * constructing one per sound leaks a hardware voice each time.
 */
import { mixer } from '../audio/engine';
import type { StripHandle } from '../audio/types';
import type { NoiseEvent } from './net';

// Declared at import so the game has a channel on the mixer before the first
// match — a fader you can only find while being shot at is not a fader.
mixer.declareStrip({ id: 'hassault', label: 'HorribleAssault', icon: '🔫' });

/** A noise that would be inaudible is not worth a voice. */
const MIN_GAIN = 0.02;

/**
 * Voices allowed to overlap.
 *
 * A firefight between eight players can produce a lot of noise in one 50 ms tick,
 * and every simultaneous voice is a real oscillator. Past this the quietest are
 * simply dropped, which is also roughly what ears do.
 */
const MAX_VOICES = 12;

interface Timbre {
  /** Centre of the band-pass, in Hz. Low is a thump, high is a snap. */
  frequency: number;
  /** How wide the band is. A narrow band rings, a wide one hisses. */
  q: number;
  /** Seconds of decay. */
  decay: number;
  /** Relative loudness, before distance falloff. */
  gain: number;
  /** A short sine thump under the noise, in Hz — 0 for none. */
  body: number;
}

/**
 * One timbre per noise kind.
 *
 * Chosen so the *kind* is identifiable without looking: a footstep is short and
 * mid-band, a landing has a low thump under it, a shot is bright and loud, death
 * is the longest sound in the game. Being able to tell a reload from a footstep
 * behind you is a real piece of information, so they are deliberately far apart.
 */
const TIMBRES: Record<string, Timbre> = {
  step: { frequency: 620, q: 1.1, decay: 0.1, gain: 0.5, body: 90 },
  land: { frequency: 380, q: 0.9, decay: 0.22, gain: 0.85, body: 62 },
  jump: { frequency: 900, q: 1.6, decay: 0.09, gain: 0.35, body: 0 },
  shot: { frequency: 1500, q: 0.7, decay: 0.3, gain: 1, body: 78 },
  reload: { frequency: 2400, q: 3.4, decay: 0.14, gain: 0.4, body: 0 },
  hurt: { frequency: 300, q: 2.2, decay: 0.3, gain: 0.7, body: 120 },
  die: { frequency: 220, q: 1.4, decay: 0.6, gain: 0.9, body: 55 },
};

const FALLBACK: Timbre = TIMBRES.step;

/**
 * A short burst of white noise, generated once and reused as the source for every
 * voice.
 *
 * Regenerating it per sound would be a few thousand `Math.random()` calls on the
 * frame a gun fires, which is exactly the frame that cannot afford them.
 */
function noiseBuffer(ctx: AudioContext): AudioBuffer {
  const length = Math.floor(ctx.sampleRate * 0.7);
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
  return buffer;
}

export class GameAudio {
  private ctx: AudioContext | null = null;
  private strip: StripHandle | null = null;
  private master: GainNode | null = null;
  private noise: AudioBuffer | null = null;
  private voices = 0;
  private volume = 1;

  /** 0 silences it entirely — and then no context is ever created. */
  setVolume(volume: number): void {
    this.volume = Math.max(0, Math.min(1, volume));
    if (this.master) this.master.gain.value = this.volume;
  }

  /**
   * Ensure a context exists, resuming one the browser suspended.
   *
   * Autoplay policy suspends a context created before any user gesture. The pane
   * only ever makes a sound after a click on the canvas (that is what takes
   * pointer lock), so by the time this runs there has been a gesture — but a
   * context created *earlier* stays suspended until asked, hence the resume.
   */
  private ready(): AudioContext | null {
    if (this.volume <= 0) return null;
    if (!this.ctx) {
      // The mixer's context, not one of our own. Web Audio nodes cannot cross
      // contexts, so a private `new AudioContext()` here would be a game whose
      // sound the user cannot route anywhere — and nothing would report that.
      const handle = mixer.connectStrip('hassault');
      this.strip = handle;
      this.ctx = handle.context;
      this.master = this.ctx.createGain();
      this.master.gain.value = this.volume;
      this.master.connect(handle.input);
      this.noise = noiseBuffer(this.ctx);
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume();
    return this.ctx;
  }

  /**
   * Play one noise.
   *
   * `listenerYaw` rotates the world bearing into the listener's own frame, which is
   * what makes the pan mean anything: a sound to your left has to move to your
   * right when you turn around.
   */
  play(kind: string, volume: number, bearing: number, listenerYaw: number, up = 0): void {
    if (volume < MIN_GAIN) return;
    if (this.voices >= MAX_VOICES) return;
    const ctx = this.ready();
    if (!ctx || !this.master || !this.noise) return;

    const timbre = TIMBRES[kind] ?? FALLBACK;
    const now = ctx.currentTime;
    const gain = volume * timbre.gain;

    const source = ctx.createBufferSource();
    source.buffer = this.noise;
    // A random offset into the shared buffer, so repeated footsteps are not
    // literally the same waveform — which is audible immediately, as a machine gun
    // rather than a person walking.
    const offset = Math.random() * 0.4;

    const band = ctx.createBiquadFilter();
    band.type = 'bandpass';
    // Sounds from above and below are tilted rather than positioned: WebAudio's
    // stereo panner has no elevation, and a filter tilt is the cue headphones
    // actually convey.
    band.frequency.value = timbre.frequency * (up > 0 ? 1.25 : up < 0 ? 0.8 : 1);
    band.Q.value = timbre.q;

    const envelope = ctx.createGain();
    envelope.gain.setValueAtTime(0, now);
    envelope.gain.linearRampToValueAtTime(gain, now + 0.004);
    envelope.gain.exponentialRampToValueAtTime(Math.max(gain * 0.001, 1e-5), now + timbre.decay);

    const panner = ctx.createStereoPanner();
    // Bearing relative to where the listener is facing; the sine of that is the
    // left/right component, which is all a stereo pan can carry.
    panner.pan.value = Math.max(-1, Math.min(1, Math.sin(bearing - listenerYaw)));

    source.connect(band);
    band.connect(envelope);
    envelope.connect(panner);
    panner.connect(this.master);

    this.voices += 1;
    source.onended = () => {
      this.voices = Math.max(0, this.voices - 1);
      source.disconnect();
      band.disconnect();
      envelope.disconnect();
      panner.disconnect();
    };
    source.start(now, offset, timbre.decay + 0.05);

    if (timbre.body > 0) {
      // A sine thump under the noise. What makes a landing feel like weight
      // arriving rather than a hiss.
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(timbre.body, now);
      osc.frequency.exponentialRampToValueAtTime(timbre.body * 0.6, now + timbre.decay);
      const thump = ctx.createGain();
      thump.gain.setValueAtTime(0, now);
      thump.gain.linearRampToValueAtTime(gain * 0.7, now + 0.006);
      thump.gain.exponentialRampToValueAtTime(1e-5, now + timbre.decay);
      osc.connect(thump);
      thump.connect(panner);
      osc.start(now);
      osc.stop(now + timbre.decay + 0.02);
      osc.onended = () => {
        osc.disconnect();
        thump.disconnect();
      };
    }
  }

  /** Play a noise the server sent us. */
  heard(event: NoiseEvent, listenerYaw: number): void {
    this.play(event.kind, event.volume, event.bearing, listenerYaw, event.up);
  }

  /**
   * Play one of *our own* noises, at full volume and dead centre.
   *
   * The server deliberately does not send these back: they need no round trip, and
   * a footstep that arrives 50 ms after the step does not sound like a footstep.
   */
  own(kind: string, volume = 1): void {
    this.play(kind, volume, 0, 0, 0);
  }

  dispose(): void {
    // Emphatically **not** `ctx.close()` any more. The context is the mixer's
    // and is shared by every sound in the app, so closing it here would silence
    // karaoke, the agent's voice and every other pane the moment a match ended.
    // Releasing the strip drops our audio and leaves the routing intact.
    this.master?.disconnect();
    this.strip?.release();
    this.strip = null;
    this.ctx = null;
    this.master = null;
    this.noise = null;
    this.voices = 0;
  }
}
