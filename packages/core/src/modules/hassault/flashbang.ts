/**
 * The flashbang, as this client draws it: how white the screen is, frame by frame.
 *
 * Named `flashbang` because `flash.ts` is the muzzle flash.
 *
 * ## The server decides; this draws
 *
 * How blind a flash leaves you is resolved on the server, per victim, from where
 * you were looking, how far away it went off and whether a wall or a smoke was
 * between you (`grenades.flash_effect`). What arrives is three durations — the
 * peak whiteness, and the **seconds left** of its hold and its fade — and this
 * file runs `flashAlpha` on them at frame rate, counting them down between
 * snapshots exactly as `MatchPlayer.step_flash` does on the server.
 *
 * That is why there is a curve here at all. The old overlay drew `you.flash`
 * squared, as received: at 20 Hz that is a staircase, and because the one number
 * set both how white and how long, a flash at medium range was a long pale wash
 * that never blinded anybody.
 *
 * `flashAlpha` exists three times — `grenades.py`, here and `flashbang.rs` — and
 * `__tests__/flash-vectors.json` binds them.
 */

/** Above this much white a bot cannot see and an ally paints nothing on the
 * radar (`grenades.FLASH_BLIND`). */
export const FLASH_BLIND = 0.6;

/**
 * How white the screen is right now, 0..1. Held at `peak`, then a smoothstep
 * down over the fade. Port of `grenades.flash_alpha`.
 */
export function flashAlpha(
  peak: number,
  holdLeft: number,
  fadeLeft: number,
  fadeTotal: number,
): number {
  if (peak <= 0) return 0;
  if (holdLeft > 0) return Math.min(1, peak);
  if (fadeLeft <= 0 || fadeTotal <= 0) return 0;
  const t = Math.max(0, Math.min(1, fadeLeft / fadeTotal));
  return Math.min(1, peak * t * t * (3 - 2 * t));
}

/** The four numbers the server sends, as seconds left. */
export interface FlashFields {
  peak: number;
  hold: number;
  fade: number;
  fadeTotal: number;
}

export const NO_FLASH: FlashFields = { peak: 0, hold: 0, fade: 0, fadeTotal: 0 };

/** The flash fields out of a snapshot's `you`, tolerating an older server that
 * sends only `flash`: that becomes a plain fade from its value, no hold. */
export function flashFieldsFrom(you: {
  flash?: number;
  flashPeak?: number;
  flashHold?: number;
  flashFade?: number;
  flashFadeTotal?: number;
}): FlashFields {
  if (you.flashPeak !== undefined) {
    return {
      peak: you.flashPeak,
      hold: you.flashHold ?? 0,
      fade: you.flashFade ?? 0,
      fadeTotal: you.flashFadeTotal ?? you.flashFade ?? 0,
    };
  }
  const flash = you.flash ?? 0;
  if (flash <= 0) return NO_FLASH;
  // An old server's linear fade lasted `flash * 6` seconds.
  return { peak: flash, hold: 0, fade: flash * 6, fadeTotal: flash * 6 };
}

/**
 * Run the hold, then the fade, down by `dt`. Port of `MatchPlayer.step_flash`,
 * including where the end of the hold spills into the fade — a client that
 * dropped the spill would sit a frame behind the server on every flash.
 */
export function stepFlash(state: FlashFields, dt: number): FlashFields {
  if (state.peak <= 0) return state;
  let { hold, fade } = state;
  let rest = dt;
  if (hold > 0) {
    const spill = rest - hold;
    hold = Math.max(0, hold - rest);
    if (spill <= 0) return { ...state, hold };
    rest = spill;
  }
  fade = Math.max(0, fade - rest);
  if (fade <= 0) return NO_FLASH;
  return { ...state, hold, fade };
}

/**
 * The flash this client is drawing: re-seeded by each snapshot, stepped by each
 * frame.
 *
 * Seeded rather than merely followed, because the snapshot is the truth and the
 * local countdown is only the bit in between — a frame that ran long or a tab
 * that slept is corrected by the next packet rather than accumulating.
 */
export class FlashState {
  private fields: FlashFields = NO_FLASH;

  /** Adopt the server's numbers. */
  seed(fields: FlashFields): void {
    this.fields = fields;
  }

  step(dt: number): void {
    if (dt > 0) this.fields = stepFlash(this.fields, dt);
  }

  get alpha(): number {
    return flashAlpha(this.fields.peak, this.fields.hold, this.fields.fade, this.fields.fadeTotal);
  }

  /** How far through the fade, 1 → 0; 1 throughout the hold. What the
   * afterimage's opacity follows. */
  get remaining(): number {
    const { peak, hold, fade, fadeTotal } = this.fields;
    if (peak <= 0) return 0;
    if (hold > 0) return 1;
    return fadeTotal > 0 ? Math.max(0, Math.min(1, fade / fadeTotal)) : 0;
  }

  get current(): FlashFields {
    return this.fields;
  }
}

// ---------------------------------------------------------------------------
// What a flash does to your ears
// ---------------------------------------------------------------------------

/**
 * The ring: a narrow band of noise high up, as the native client's `tinnitus`
 * timbre has always been. `browser_parity.rs` pins these four to `flashbang.rs`.
 */
export const FLASH_RING_HZ = 4100;
export const FLASH_RING_Q = 12;
export const FLASH_RING_GAIN = 0.7;
export const FLASH_RING_BODY = 3850;

/** The world's sound through the white: a low-pass from open down to this. */
export const FLASH_MUFFLE_HZ = 700;
/** "Open" — above anything the game's synthesized sounds put out. */
export const FLASH_OPEN_HZ = 18000;

/** Flashes shorter than this in all do not ring: a flicker is not a bang in
 * your ear. */
export const FLASH_RING_MIN = 0.75;

/**
 * Whether a flash rings, how loud and for how long. Tied to the flash's own
 * length so the ringing and the white clear together — a fixed-length ring was
 * either over before you could see or still going long after.
 */
export function flashRing(
  peak: number,
  hold: number,
  fade: number,
): { volume: number; seconds: number } | null {
  if (peak <= 0 || hold + fade < FLASH_RING_MIN) return null;
  return { volume: Math.max(0.5, Math.min(1, peak)), seconds: 0.6 + hold + 0.5 * fade };
}

/**
 * The low-pass cutoff for the world's sound at a given whiteness. Interpolated
 * in log frequency, which is how an ear hears it: a linear sweep spends almost
 * all of its range sounding open.
 *
 * A gameplay effect, not mixing taste: being flashed costs you your ears as well
 * as your eyes, which is most of why a flash before a push works. That is why it
 * is not the "ducking" the mixer has a recorded decision against.
 */
export function muffleHz(alpha: number): number {
  const a = Math.max(0, Math.min(1, alpha));
  return FLASH_OPEN_HZ * Math.pow(FLASH_MUFFLE_HZ / FLASH_OPEN_HZ, a);
}
