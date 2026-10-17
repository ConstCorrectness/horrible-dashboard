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
import {
  FLASH_RING_BODY,
  FLASH_RING_GAIN,
  FLASH_RING_HZ,
  FLASH_RING_Q,
  muffleHz,
} from './flashbang';
import { mixer } from '../audio/engine';
import type { StripHandle } from '../audio/types';
import type { WeaponSpec } from './api';
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
import { getCachedAssetUrl } from './models/assetCache';

export type CalloutKind =
  | 'spotted'
  | 'watch'
  | 'danger'
  | 'utility'
  | 'reloading'
  | 'fire_in_the_hole'
  | 'flashbang'
  | 'smoke_out'
  | 'bomb_planted'
  | 'bomb_defused';

/** All 26 deterministic .wav assets shipped in `/sounds/hassault/<name>.wav`. */
export const WAV_SOUNDS = [
  'step_concrete_1',
  'step_concrete_2',
  'step_metal_1',
  'step_metal_2',
  'step_wood_1',
  'step_wood_2',
  'step_sand_1',
  'step_sand_2',
  'step_tile_1',
  'step_tile_2',
  'land',
  'jump',
  'reload_magout',
  'reload_magin',
  'reload_bolt',
  'reload',
  'callout_spotted',
  'callout_watch',
  'callout_danger',
  'callout_utility',
  'callout_reloading',
  'callout_fire_in_the_hole',
  'callout_flashbang',
  'callout_smoke_out',
  'callout_bomb_planted',
  'callout_bomb_defused',
] as const;

export type WavSoundName = (typeof WAV_SOUNDS)[number];

/**
 * Resolves the surface-appropriate footstep sound kind (`step_concrete`, `step_metal`,
 * `step_wood`, `step_sand`, `step_tile`) from the active map and player coordinates.
 */
export function footstepKindForMap(
  mapName: string,
  x: number,
  y: number,
  z: number,
  isVent = false,
): 'step_concrete' | 'step_metal' | 'step_wood' | 'step_sand' | 'step_tile' {
  if (isVent) return 'step_metal';
  if (mapName === 'hd_dust2' || mapName === 'hd_mirage') {
    // Elevated wooden palace / catwalk / crate platforms vs desert sand & sandstone
    if (z > 3.5) return 'step_wood';
    return 'step_sand';
  }
  if (mapName === 'hd_bank') {
    // Bank interior lobby (x: 4..60, y: 18..60 or scaled 12..180, 54..180) has polished marble tile
    const scale = x > 64 || y > 64 ? 3 : 1;
    const mx = x / scale;
    const my = y / scale;
    const mz = z / scale;
    if (mz >= 4.5 && (mx < 21 || mx > 43)) return 'step_wood'; // Executive mahogany mezzanine
    if (mx >= 40 && my >= 48) return 'step_metal'; // Steel bank vault floor
    if (my >= 18) return 'step_tile'; // Grand marble lobby
    return 'step_concrete'; // Exterior asphalt street
  }
  if (mapName === 'hd_office') {
    return 'step_tile';
  }
  if (mapName === 'hd_nuke') {
    if (z < -2.0 || z > 9.0) return 'step_metal'; // Lower B reactor grate & upper catwalk
    return 'step_concrete';
  }
  if (mapName === 'hd_facility' || mapName === 'hd_assault') {
    if (z > 3.5) return 'step_metal'; // Elevated steel catwalks & overpass
    return 'step_concrete';
  }
  if (mapName === 'hd_junkflea') {
    if (z > 2.5) return 'step_metal'; // Shipping container roofs & steel bridges
    return 'step_sand';
  }
  if (mapName === 'hd_inferno') {
    if (z > 2.8) return 'step_wood'; // Apartments & balcony wood floors
    return 'step_tile';
  }
  return 'step_concrete';
}

const TIMBRES: Record<string, Timbre> = {
  step: { frequency: 620, q: 1.1, decay: 0.1, gain: 0.5, body: 90 },
  step_concrete: { frequency: 620, q: 1.1, decay: 0.1, gain: 0.5, body: 90 },
  step_duct: { frequency: 1850, q: 6.5, decay: 0.22, gain: 0.65, body: 320 },
  step_metal: { frequency: 1850, q: 6.5, decay: 0.22, gain: 0.65, body: 320 },
  step_wood: { frequency: 480, q: 2.2, decay: 0.13, gain: 0.55, body: 135 },
  step_sand: { frequency: 2400, q: 1.2, decay: 0.14, gain: 0.48, body: 75 },
  step_tile: { frequency: 2800, q: 2.4, decay: 0.11, gain: 0.52, body: 105 },
  land: { frequency: 380, q: 0.9, decay: 0.22, gain: 0.85, body: 62 },
  jump: { frequency: 900, q: 1.6, decay: 0.09, gain: 0.35, body: 0 },
  shot: { frequency: 1500, q: 0.7, decay: 0.3, gain: 1, body: 78 },
  reload: { frequency: 2400, q: 3.4, decay: 0.14, gain: 0.4, body: 0 },
  reload_magout: { frequency: 1600, q: 2.8, decay: 0.12, gain: 0.55, body: 45 },
  reload_magin: { frequency: 2100, q: 3.2, decay: 0.15, gain: 0.7, body: 85 },
  reload_bolt: { frequency: 2900, q: 4.0, decay: 0.18, gain: 0.75, body: 50 },
  knife_slash: { frequency: 2800, q: 2.5, decay: 0.11, gain: 0.5, body: 0 },
  knife_stab: { frequency: 1800, q: 1.8, decay: 0.18, gain: 0.8, body: 110 },
  // An item leaving the map. Bright, short and unmistakably not a gun: the
  // sound has to be legible as "somebody took the armour" from across a room,
  // where a dull one would be mistaken for a footstep.
  pickup: { frequency: 1650, q: 5.5, decay: 0.16, gain: 0.45, body: 0 },
  // Breaking the surface. Low Q so the burst stays broadband — a splash is
  // noise, and a narrow filter turns it into a bell.
  splash: { frequency: 900, q: 0.6, decay: 0.34, gain: 0.75, body: 40 },
  hurt: { frequency: 300, q: 2.2, decay: 0.3, gain: 0.7, body: 120 },
  die: { frequency: 220, q: 1.4, decay: 0.6, gain: 0.9, body: 55 },
  // A grenade leaving the hand: a short soft rush, quieter than a footstep so it
  // is a cue for the thrower rather than an announcement to the room.
  throw: { frequency: 1100, q: 1.2, decay: 0.12, gain: 0.3, body: 0 },
  // The four detonations, and they have to be tellable apart with your back
  // turned — which is most of what a grenade is: information.
  //
  // **Low and long is an explosion; high and short is a bang.** The HE gets the
  // longest decay and the most body in the room, the flash is a crack with no
  // weight behind it, the smoke is a hiss, and fire is a low roar.
  explosion: { frequency: 90, q: 0.5, decay: 0.9, gain: 1, body: 150 },
  nade_flash: { frequency: 2600, q: 0.6, decay: 0.35, gain: 1, body: 30 },
  nade_smoke: { frequency: 4200, q: 0.35, decay: 0.85, gain: 0.5, body: 0 },
  nade_fire: { frequency: 210, q: 0.45, decay: 1.1, gain: 0.7, body: 70 },
  // Headshot feedback: high-frequency metallic ping for armored helmet hit, wet snap for flesh
  helmet_dink: { frequency: 3200, q: 7.5, decay: 0.16, gain: 0.95, body: 1400 },
  flesh_headshot: { frequency: 1900, q: 2.2, decay: 0.22, gain: 0.85, body: 95 },
  // Multikill clutch audio fanfares
  multikill_double: { frequency: 580, q: 2.0, decay: 0.35, gain: 0.7, body: 320 },
  multikill_triple: { frequency: 720, q: 2.5, decay: 0.42, gain: 0.8, body: 440 },
  multikill_quad: { frequency: 880, q: 3.0, decay: 0.50, gain: 0.9, body: 580 },
  multikill_ace: { frequency: 1100, q: 3.8, decay: 0.75, gain: 1.0, body: 880 },
  // Tactical Ping & Callouts: synthesized radio chirps and alert tones
  ping_spotted: { frequency: 1350, q: 4.5, decay: 0.20, gain: 0.95, body: 950 },
  ping_watch: { frequency: 780, q: 3.2, decay: 0.16, gain: 0.7, body: 650 },
  ping_utility: { frequency: 540, q: 2.8, decay: 0.18, gain: 0.75, body: 440 },
  ping_danger: { frequency: 460, q: 2.5, decay: 0.28, gain: 0.9, body: 320 },
  callout_spotted: { frequency: 1350, q: 4.5, decay: 0.25, gain: 0.9, body: 950 },
  callout_watch: { frequency: 780, q: 3.2, decay: 0.22, gain: 0.85, body: 650 },
  callout_danger: { frequency: 460, q: 2.5, decay: 0.28, gain: 0.9, body: 320 },
  callout_utility: { frequency: 540, q: 2.8, decay: 0.22, gain: 0.85, body: 440 },
  callout_reloading: { frequency: 1150, q: 3.0, decay: 0.22, gain: 0.75, body: 520 },
  callout_fire_in_the_hole: { frequency: 1420, q: 3.5, decay: 0.26, gain: 0.9, body: 680 },
  callout_flashbang: { frequency: 1650, q: 3.8, decay: 0.24, gain: 0.88, body: 740 },
  callout_smoke_out: { frequency: 980, q: 2.6, decay: 0.22, gain: 0.85, body: 480 },
  callout_bomb_planted: { frequency: 620, q: 3.0, decay: 0.35, gain: 0.95, body: 380 },
  callout_bomb_defused: { frequency: 880, q: 3.2, decay: 0.35, gain: 0.95, body: 520 },
  // Competitive Bomb Defuse & Clutch Audio
  heartbeat: { frequency: 65, q: 1.5, decay: 0.18, gain: 0.85, body: 55 },
  defuse_wire_cut: { frequency: 1800, q: 4.5, decay: 0.08, gain: 0.9, body: 240 },
  clutch_fanfare: { frequency: 820, q: 2.2, decay: 0.65, gain: 0.95, body: 440 },
  bomb_beep: { frequency: 2500, q: 8.0, decay: 0.06, gain: 0.8, body: 1200 },
  // Surface Material Impacts & Penetration Thump
  impact_concrete: { frequency: 1800, q: 2.2, decay: 0.08, gain: 0.55, body: 180 },
  impact_metal: { frequency: 3400, q: 8.5, decay: 0.14, gain: 0.65, body: 1200 },
  impact_wood: { frequency: 580, q: 3.0, decay: 0.10, gain: 0.60, body: 120 },
  impact_glass: { frequency: 4200, q: 4.5, decay: 0.12, gain: 0.70, body: 300 },
  impact_wallbang: { frequency: 280, q: 2.0, decay: 0.18, gain: 0.85, body: 80 },
  glass_shatter: { frequency: 5400, q: 3.5, decay: 0.48, gain: 0.95, body: 220 },
};

const FALLBACK: Timbre = TIMBRES.step;

/**
 * A weapon's own voice, **derived from its balance numbers** rather than given a
 * table of its own.
 *
 * The same reasoning as `shot_loudness` scaling with damage: tie the sound to the
 * numbers the weapon already has and a balance change cannot silently leave the
 * audio describing the previous version of the gun. It also means a weapon added
 * to `weapons.py` arrives with a voice and no client release.
 *
 * What each number is doing to the ear:
 *
 * - **`rpm` picks the pitch.** A fast, small weapon cracks; a slow, heavy one
 *   booms. This is the cue that separates a rifle from a sniper at distance.
 * - **`pellets` widens the band.** More than one pellet is a shotgun, and a wide
 *   band is a blast rather than a bang.
 * - **`damage` sets the body and the decay.** Weight and how long it hangs.
 *
 * The knife is the one special case, told apart exactly as `shot_loudness` tells
 * it: no kickback and almost no range. A swing is a short quiet swish, and it has
 * to *not* sound like a gun, because being able to hear that somebody is holding
 * a knife is the reason its silence is worth anything.
 */
export function weaponVoice(weapon: WeaponSpec | undefined): Timbre {
  if (!weapon) return TIMBRES.shot;
  if (weapon.kickback <= 0 && weapon.range <= 6) {
    return { frequency: 2600, q: 2.2, decay: 0.12, gain: 0.45, body: 0 };
  }
  const fast = Math.min(1, weapon.rpm / 700);
  const spread = weapon.pellets > 1;
  const heft = Math.min(1, weapon.damage / 90);
  return {
    frequency: 900 + fast * 1100,
    q: spread ? 0.35 : 0.75,
    decay: 0.16 + heft * 0.2,
    gain: 1,
    body: 95 - heft * 42,
  };
}

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
  /**
   * The world's sound goes through this on its way out: open, except while a
   * flashbang has you (`setMuffle`). The ring itself goes round it — a filter
   * that muffled the tinnitus too would muffle the one sound a flash adds.
   */
  private muffle: BiquadFilterNode | null = null;
  private dry: GainNode | null = null;
  private muffleAlpha = 0;
  private noise: AudioBuffer | null = null;
  private wavBuffers = new Map<string, AudioBuffer>();
  private wavPreloaded = false;
  private stepToggle = 0;
  private lastCalloutAt = -Infinity;
  private lastPlayedAt = new Map<string, number>();
  private activeCalloutSource: AudioBufferSourceNode | null = null;
  private voices = 0;
  private volume = 1;

  private cooldownForKind(kind: string): [string, number] | null {
    if (kind === 'step') return ['heard_step', 220];
    if (kind.startsWith('step_')) return ['own_step', 260];
    if (kind === 'land' || kind === 'jump') return [kind, 280];
    if (kind === 'reload' || kind.startsWith('reload_')) return [kind, 350];
    if (kind.startsWith('callout_') || kind.startsWith('ping_')) return ['callout', 900];
    return null;
  }

  /** 0 silences it entirely — and then no context is ever created. */
  setVolume(volume: number): void {
    this.volume = Math.max(0, Math.min(1, volume));
    if (this.master) this.master.gain.value = this.volume;
    if (this.dry) this.dry.gain.value = this.volume;
  }

  /**
   * Muffle the world by how white the screen is, 0..1 — the flashbang's
   * deafness. Called per frame with the flash's current alpha; cheap when it has
   * not moved, and smoothed so a 20 Hz seed does not zipper the cutoff.
   */
  setMuffle(alpha: number): void {
    const a = Math.max(0, Math.min(1, alpha));
    if (Math.abs(a - this.muffleAlpha) < 0.005) return;
    this.muffleAlpha = a;
    if (!this.ctx || !this.muffle) return;
    this.muffle.frequency.setTargetAtTime(muffleHz(a), this.ctx.currentTime, 0.03);
  }

  /**
   * The flashbang's ring: the native client's `tinnitus` voice, a narrow band of
   * noise with a high sine under it, lasting as long as the flash. Played dry,
   * round the muffle.
   */
  flashRing(volume: number, seconds: number): void {
    const ctx = this.ready();
    if (!ctx || !this.dry || !this.noise) return;
    const now = ctx.currentTime;
    const gain = volume * FLASH_RING_GAIN;
    const source = ctx.createBufferSource();
    source.buffer = this.noise;
    source.loop = true;
    const band = ctx.createBiquadFilter();
    band.type = 'bandpass';
    band.frequency.value = FLASH_RING_HZ;
    band.Q.value = FLASH_RING_Q;
    const envelope = ctx.createGain();
    // The same 4 ms attack into an exponential decay as every other voice, with
    // the decay reaching silence at `seconds` (e^-6 is the native envelope's
    // end point).
    envelope.gain.setValueAtTime(0, now);
    envelope.gain.linearRampToValueAtTime(gain, now + 0.004);
    envelope.gain.setTargetAtTime(0, now + 0.004, seconds / 6);
    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = FLASH_RING_BODY;
    const tone = ctx.createGain();
    tone.gain.value = 0.7;
    source.connect(band);
    band.connect(envelope);
    osc.connect(tone);
    tone.connect(envelope);
    envelope.connect(this.dry);
    source.start(now);
    osc.start(now);
    const end = now + seconds + 0.05;
    source.stop(end);
    osc.stop(end);
    osc.onended = () => {
      source.disconnect();
      band.disconnect();
      osc.disconnect();
      tone.disconnect();
      envelope.disconnect();
    };
  }

  private preloadWavBuffers(ctx: AudioContext): void {
    if (this.wavPreloaded || typeof fetch !== 'function' || typeof ctx.decodeAudioData !== 'function') {
      return;
    }
    this.wavPreloaded = true;
    for (const name of WAV_SOUNDS) {
      void (async () => {
        try {
          const url = await getCachedAssetUrl(`/sounds/hassault/${name}.wav`);
          const res = await fetch(url);
          if (!res.ok) return;
          const ab = await res.arrayBuffer();
          const decoded = await ctx.decodeAudioData(ab);
          this.wavBuffers.set(name, decoded);
        } catch {
          // Headless / offline test environments silently fall back to procedural synthesis
        }
      })();
    }
  }

  private resolveWavBuffer(kind: string): AudioBuffer | undefined {
    const variant = (this.stepToggle = 1 - this.stepToggle) + 1;
    switch (kind) {
      case 'step':
      case 'step_concrete':
        return this.wavBuffers.get(`step_concrete_${variant}`);
      case 'step_duct':
      case 'step_metal':
        return this.wavBuffers.get(`step_metal_${variant}`);
      case 'step_wood':
        return this.wavBuffers.get(`step_wood_${variant}`);
      case 'step_sand':
        return this.wavBuffers.get(`step_sand_${variant}`);
      case 'step_tile':
        return this.wavBuffers.get(`step_tile_${variant}`);
      case 'land':
      case 'jump':
      case 'reload':
      case 'reload_magout':
      case 'reload_magin':
      case 'reload_bolt':
        return this.wavBuffers.get(kind);
      case 'ping_spotted':
      case 'callout_spotted':
        return this.wavBuffers.get('callout_spotted');
      case 'ping_watch':
      case 'callout_watch':
        return this.wavBuffers.get('callout_watch');
      case 'ping_danger':
      case 'callout_danger':
        return this.wavBuffers.get('callout_danger');
      case 'ping_utility':
      case 'callout_utility':
        return this.wavBuffers.get('callout_utility');
      default:
        if (kind.startsWith('callout_')) {
          return this.wavBuffers.get(kind);
        }
        return undefined;
    }
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
      this.muffle = this.ctx.createBiquadFilter();
      this.muffle.type = 'lowpass';
      this.muffle.Q.value = 0.7;
      this.muffle.frequency.value = muffleHz(this.muffleAlpha);
      this.master.connect(this.muffle);
      this.muffle.connect(handle.input);
      this.dry = this.ctx.createGain();
      this.dry.gain.value = this.volume;
      this.dry.connect(handle.input);
      this.noise = noiseBuffer(this.ctx);
      this.preloadWavBuffers(this.ctx);
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
  play(
    kind: string,
    volume: number,
    bearing: number,
    listenerYaw: number,
    up = 0,
    // The weapon that made it, for a shot. A voice, not a *kind*: a shot is a
    // shot however it sounds, and keying the timbre table by `shot:sniper` would
    // make an unknown weapon fall all the way back to a footstep.
    voice?: Timbre,
    occluded = false,
  ): void {
    if (volume < MIN_GAIN) return;
    if (this.voices >= MAX_VOICES) return;
    const cd = !voice ? this.cooldownForKind(kind) : null;
    if (cd) {
      const wallNow = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const [cdKey, minMs] = cd;
      const prevAt = this.lastPlayedAt.get(cdKey) ?? -Infinity;
      if (wallNow - prevAt < minMs) return;
      this.lastPlayedAt.set(cdKey, wallNow);
    }
    const ctx = this.ready();
    if (!ctx || !this.master || !this.noise) return;

    const timbre = voice ?? TIMBRES[kind] ?? FALLBACK;
    const now = ctx.currentTime;
    let gain = volume * timbre.gain;
    if (occluded) gain *= 0.65;

    // Prefer high-definition .wav sample buffer when loaded for footsteps, reloads & callouts
    const wavBuf = !voice ? this.resolveWavBuffer(kind) : undefined;
    if (wavBuf) {
      const isCallout = kind.startsWith('callout_') || kind.startsWith('ping_');
      if (isCallout && this.activeCalloutSource) {
        try {
          this.activeCalloutSource.stop();
        } catch {
          // Ignore if already stopped
        }
        this.activeCalloutSource = null;
      }

      const source = ctx.createBufferSource();
      source.buffer = wavBuf;
      source.loop = false;
      const jitter = isCallout ? 1.0 : 0.96 + Math.random() * 0.08;
      const elevTilt = up > 0 ? 1.05 : up < 0 ? 0.95 : 1.0;
      source.playbackRate.value = jitter * elevTilt;

      let nodeOut: AudioNode = source;
      let occludeFilter: BiquadFilterNode | null = null;
      if (occluded) {
        occludeFilter = ctx.createBiquadFilter();
        occludeFilter.type = 'lowpass';
        occludeFilter.frequency.value = 750;
        source.connect(occludeFilter);
        nodeOut = occludeFilter;
      }

      const gainNode = ctx.createGain();
      gainNode.gain.setValueAtTime(gain, now);

      const panner = ctx.createStereoPanner();
      panner.pan.value = Math.max(-1, Math.min(1, Math.sin(bearing - listenerYaw)));

      nodeOut.connect(gainNode);
      gainNode.connect(panner);
      panner.connect(this.master);

      this.voices += 1;
      if (isCallout) {
        this.activeCalloutSource = source;
      }
      source.onended = () => {
        this.voices = Math.max(0, this.voices - 1);
        if (this.activeCalloutSource === source) {
          this.activeCalloutSource = null;
        }
        source.disconnect();
        if (occludeFilter) occludeFilter.disconnect();
        gainNode.disconnect();
        panner.disconnect();
      };
      source.start(now, 0, wavBuf.duration);
      return;
    }

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

    let filterOut: AudioNode = band;
    let occludeFilter: BiquadFilterNode | null = null;
    if (occluded) {
      // Wall occlusion: direct sound passes through solid geometry, muffled via lowpass
      occludeFilter = ctx.createBiquadFilter();
      occludeFilter.type = 'lowpass';
      occludeFilter.frequency.value = 650;
      band.connect(occludeFilter);
      filterOut = occludeFilter;
    }

    const envelope = ctx.createGain();
    envelope.gain.setValueAtTime(0, now);
    envelope.gain.linearRampToValueAtTime(gain, now + 0.004);
    envelope.gain.exponentialRampToValueAtTime(Math.max(gain * 0.001, 1e-5), now + timbre.decay);

    const panner = ctx.createStereoPanner();
    // Bearing relative to where the listener is facing; the sine of that is the
    // left/right component, which is all a stereo pan can carry.
    panner.pan.value = Math.max(-1, Math.min(1, Math.sin(bearing - listenerYaw)));

    source.connect(band);
    filterOut.connect(envelope);
    envelope.connect(panner);
    panner.connect(this.master);

    this.voices += 1;
    source.onended = () => {
      this.voices = Math.max(0, this.voices - 1);
      source.disconnect();
      band.disconnect();
      if (occludeFilter) occludeFilter.disconnect();
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

  /**
   * Play a noise the server sent us.
   *
   * `weapons` is the served loadout, so a shot's `weapon` id can be turned into
   * that gun's voice. Without it the shot still plays, in the generic voice —
   * an unrecognised weapon must be audible, since the alternative is a gunshot
   * you cannot hear because the client is out of date.
   */
  heard(
    event: NoiseEvent,
    listenerYaw: number,
    weapons: WeaponSpec[] = [],
    occluded = false,
  ): void {
    const voice =
      event.kind === 'shot' && event.weapon
        ? weaponVoice(weapons.find((w) => w.id === event.weapon))
        : undefined;
    const vol = event.kind === 'step' ? event.volume * 0.65 : event.volume;
    this.play(event.kind, vol, event.bearing, listenerYaw, event.up, voice, occluded);
  }

  /**
   * Play one of *our own* noises, at full volume and dead centre.
   *
   * The server deliberately does not send these back: they need no round trip, and
   * a footstep that arrives 50 ms after the step does not sound like a footstep.
   */
  own(kind: string, volume = 1, weapon?: WeaponSpec): void {
    this.play(kind, volume, 0, 0, 0, weapon ? weaponVoice(weapon) : undefined);
  }

  /**
   * Play a tactical radio voice callout (`callout_<kind>.wav`).
   * Rate-limited so overlapping events don't talk over each other.
   */
  callout(kind: CalloutKind, bearing?: number, listenerYaw = 0, volume = 0.85): void {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    if (now - this.lastCalloutAt < 900) return;
    this.lastCalloutAt = now;
    const calloutKind = `callout_${kind}`;
    if (bearing !== undefined) {
      this.play(calloutKind, volume, bearing, listenerYaw);
    } else {
      this.own(calloutKind, volume);
    }
  }

  /**
   * Play a tactical ping / callout audio cue.
   *
   * If bearing and listenerYaw are provided, the radio voice callout is positioned
   * stereophonically; otherwise it plays front-and-centre.
   */
  ping(kind: string, bearing?: number, listenerYaw = 0): void {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    if (now - this.lastCalloutAt < 900) return;
    this.lastCalloutAt = now;
    const pingKind = `ping_${kind}`;
    if (bearing !== undefined) {
      this.play(pingKind, 0.85, bearing, listenerYaw);
    } else {
      this.own(pingKind, 0.85);
    }
  }

  /**
   * Wire snip metallic click played at defusal progress milestones.
   */
  defuseWireCut(): void {
    this.own('defuse_wire_cut', 0.9);
  }

  /**
   * Tension heartbeat (sub-bass 65 Hz) played during critical defuses / clutch situations.
   */
  heartbeat(intensity = 0.8): void {
    this.own('heartbeat', Math.max(0.2, Math.min(1.0, intensity)));
  }

  /**
   * C4 explosive countdown beep, panning stereophonically if bomb bearing is known.
   */
  bombBeep(volume = 0.8, bearing?: number, listenerYaw = 0): void {
    if (bearing !== undefined) {
      this.play('bomb_beep', volume, bearing, listenerYaw);
    } else {
      this.own('bomb_beep', volume);
    }
  }

  /**
   * Triumphant clutch fanfare on defusal or ninja clutch.
   */
  clutchFanfare(isNinja = false): void {
    const ctx = this.ready();
    if (!ctx || !this.master) return;
    this.own('clutch_fanfare', 1.0);
    if (isNinja) {
      // Play second higher-pitched shimmer for ninja defuse
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(1100, now + 0.12);
      osc.frequency.exponentialRampToValueAtTime(1760, now + 0.45);
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now + 0.12);
      gain.gain.linearRampToValueAtTime(0.5, now + 0.16);
      gain.gain.exponentialRampToValueAtTime(1e-4, now + 0.65);
      osc.connect(gain);
      gain.connect(this.master);
      osc.start(now + 0.12);
      osc.stop(now + 0.7);
      osc.onended = () => {
        osc.disconnect();
        gain.disconnect();
      };
    }
  }

  /**
   * Play surface material impact audio for bullet-hitting-wall feedback.
   *
   * Uses bearing + listenerYaw like every other positional sound. The call site
   * computes bearing from the impact position and the player's position; details
   * of stereo placement are handled by `play()`.
   */
  surfaceImpact(
    material: 'concrete' | 'metal' | 'wood' | 'glass' | 'default',
    bearing: number,
    listenerYaw: number,
    volume = 0.6,
  ): void {
    const sound =
      material === 'metal'
        ? 'impact_metal'
        : material === 'wood'
          ? 'impact_wood'
          : material === 'glass'
            ? 'impact_glass'
            : 'impact_concrete';
    this.play(sound, volume, bearing, listenerYaw);
  }

  /**
   * Play crystal glass shattering burst sound.
   */
  glassShatter(bearing: number, listenerYaw: number): void {
    this.play('glass_shatter', 0.95, bearing, listenerYaw);
    this.surfaceImpact('glass', bearing, listenerYaw, 0.7);
  }

  dispose(): void {
    // Emphatically **not** `ctx.close()` any more. The context is the mixer's
    // and is shared by every sound in the app, so closing it here would silence
    // karaoke, the agent's voice and every other pane the moment a match ended.
    // Releasing the strip drops our audio and leaves the routing intact.
    this.master?.disconnect();
    this.muffle?.disconnect();
    this.dry?.disconnect();
    this.strip?.release();
    this.strip = null;
    this.ctx = null;
    this.master = null;
    this.muffle = null;
    this.dry = null;
    this.noise = null;
    this.voices = 0;
  }
}
