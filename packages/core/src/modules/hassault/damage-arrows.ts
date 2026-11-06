/**
 * Arcs around the crosshair pointing at whatever is shooting you.
 *
 * The port of `paint_damage_arrows` in `apps/native-fps/src/hud.rs`, and the
 * one piece of the HUD that is an *instruction* rather than a report: a hurt
 * vignette says you are being shot, which you already knew, while an arrow says
 * which way to turn.
 *
 * Fed by `you.hurt`, which the server drains per snapshot like `hits`, so each
 * marker arrives exactly once. The bearing is the server's (`atan2(dy, dx)` to
 * the attacker, the same convention as a noise's), so the drawing is
 * `bearing - yaw` and nothing else: this client never learns where the shooter
 * is, only which way they were. Same contract as `NoiseRing`.
 *
 * The constants and the geometry are native's, in HUD units (`hudUnit`), so an
 * arrow is the same size and in the same place in both clients.
 */

/** How long an arrow stays on screen, ms. Native `ARROW_LIFE` (1.2 s). */
export const DAMAGE_ARROW_LIFE_MS = 1200;

/**
 * At most this many at once, newest kept. A shotgun is eight pellets and eight
 * `hurt` entries from one trigger, and eight stacked arrows say nothing one does
 * not.
 */
export const MAX_DAMAGE_ARROWS = 6;

/** Half the arrow's base, as an angle either side of the bearing. */
export const ARROW_SPREAD_RAD = 0.22;

/** Inner radius of the arrow's base, in HUD units: about a tenth of the screen. */
export const ARROW_RADIUS_UNITS = 26;

/** How far past the base the tip sits, in HUD units. */
export const ARROW_LENGTH_UNITS = 7;

/** Peak opacity of a fresh arrow. */
export const ARROW_ALPHA = 0.85;

export interface HurtMarker {
  /** World bearing to the attacker, radians. Subtract the view yaw to draw it. */
  bearing: number;
  /** What the hit cost, health and armour together. */
  amount: number;
}

export interface DamageArrow {
  id: number;
  /** `Date.now()` when it arrived. */
  at: number;
  bearing: number;
}

/**
 * The live arrows after a snapshot's `hurt` list: expired ones dropped, the new
 * ones appended, capped at `MAX_DAMAGE_ARROWS` keeping the newest.
 *
 * Returns `prev` itself when nothing changed, so a caller holding it in React
 * state does not re-render on every snapshot that carried no damage.
 */
export function pushHurt(
  prev: readonly DamageArrow[],
  hurt: readonly HurtMarker[] | undefined,
  now: number,
): readonly DamageArrow[] {
  const live = prev.filter((a) => now - a.at < DAMAGE_ARROW_LIFE_MS);
  if (!hurt || hurt.length === 0) return live.length === prev.length ? prev : live;
  const added = hurt
    .filter((h) => Number.isFinite(h.bearing))
    .map((h, i) => ({ id: now * 100 + i, at: now, bearing: h.bearing }));
  const next = [...live, ...added];
  return next.length > MAX_DAMAGE_ARROWS ? next.slice(next.length - MAX_DAMAGE_ARROWS) : next;
}

/**
 * Opacity of an arrow `ageMs` old. Squared, so it is bright for the moment it
 * matters and then gets out of the way rather than lingering at half strength.
 */
export function arrowAlpha(ageMs: number): number {
  const fade = Math.min(1, Math.max(0, 1 - ageMs / DAMAGE_ARROW_LIFE_MS));
  return ARROW_ALPHA * fade * fade;
}

/**
 * The arrow's triangle in screen pixels, relative to the crosshair: `[tip,
 * left, right]`, each `[x, y]` with +x right and **+y down**.
 *
 * Forward (`bearing == yaw`) has to come out *above* the crosshair. Getting the
 * sign wrong points every arrow at the exact opposite of the shooter, which is
 * worse than drawing nothing and looks like working code — `damage-arrows.test.ts`
 * pins all four quarters.
 */
export function arrowTriangle(
  bearing: number,
  yaw: number,
  unit: number,
): [[number, number], [number, number], [number, number]] {
  const rel = bearing - yaw;
  const radius = unit * ARROW_RADIUS_UNITS;
  const tipR = radius + unit * ARROW_LENGTH_UNITS;
  const at = (angle: number, r: number): [number, number] => [
    Math.sin(angle) * r,
    -Math.cos(angle) * r,
  ];
  return [at(rel, tipR), at(rel - ARROW_SPREAD_RAD, radius), at(rel + ARROW_SPREAD_RAD, radius)];
}
