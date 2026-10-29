/**
 * The radar's arithmetic: what is in view, where it lands, how big it is drawn
 * and in what colour. No canvas, no React, no wire.
 *
 * Pulled out of `panels/Radar.tsx` because there are two radars — this one and
 * the native client's (`apps/native-fps/src/radar.rs` + `hud.rs`) — and they
 * used to agree only by comment. `hassault-two-clients.mdx` listed "radar span,
 * colours, blip sizes" as unpinned drift; everything here is now written out to
 * `__tests__/radar-vectors.json` by `radar-view.test.ts` and replayed by
 * `radar.rs`'s conformance module, so the same contact cannot be a different
 * distance from the centre depending on which client you launched.
 *
 * Every size is a **fraction of the instrument's radius**, not pixels: the two
 * clients measure the screen differently (CSS pixels here, HUD units there), and
 * a fraction is the one unit that means the same picture in both.
 */

/** Cubes across the instrument at the default zoom. */
export const BASE_SPAN = 110;
/** CS's `cl_radar_scale` default, at which the span is `BASE_SPAN`. */
export const DEFAULT_SCALE = 0.7;
/** The scale the radar-zoom key holds while pressed: about 1.75× the map. */
export const ZOOM_SCALE = 0.4;
/** The icon scale at which every blip is the size it was before the setting. */
export const DEFAULT_ICON_SCALE = 0.6;

/**
 * Seconds a gunshot stays on the radar. The server's `HEARD_SECONDS` — the
 * dots arrive with their age, so this is only what the fade divides by.
 */
export const HEARD_SECONDS = 2.5;
/** Seconds a footstep blob stays on the radar. */
export const SOUND_PING_SECONDS = 1.5;
/** Seconds a lost contact keeps its last-known marker. */
export const LAST_KNOWN_SECONDS = 3;
/** How many brightness steps the floor plan is cut into, low to high. */
export const FLOOR_BANDS = 6;
/** World-space spacing of the faint grid, in cubes. */
export const GRID_CUBES = 16;
/** Radius of a footstep blob in cubes: wide on purpose, it is "about there". */
export const SOUND_PING_CUBES = 4;

export interface RadarSettings {
  scale: number;
  hudScale: number;
  iconScale: number;
  centered: boolean;
  rotate: boolean;
  fullOnScores: boolean;
  soundPings: boolean;
}

/** The setting rows, in `settings_schema.py`'s order and with its defaults. */
export const RADAR_KEYS = {
  scale: 'hassault.radar.scale',
  hudScale: 'hassault.radar.hudScale',
  iconScale: 'hassault.radar.iconScale',
  centered: 'hassault.radar.centered',
  rotate: 'hassault.radar.rotate',
  fullOnScores: 'hassault.radar.fullOnScores',
  soundPings: 'hassault.radar.soundPings',
} as const;

export const RADAR_DEFAULTS: RadarSettings = {
  scale: DEFAULT_SCALE,
  hudScale: 1,
  iconScale: DEFAULT_ICON_SCALE,
  centered: true,
  rotate: true,
  fullOnScores: true,
  soundPings: true,
};

const RANGES = {
  scale: [0.3, 1.3],
  hudScale: [0.8, 1.3],
  iconScale: [0.4, 1],
} as const;

function clampNum(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = typeof v === 'number' ? v : Number(v);
  if (v === undefined || v === null || v === '' || !Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, n));
}

function asFlag(v: unknown, fallback: boolean): boolean {
  return typeof v === 'boolean' ? v : fallback;
}

/** The settings, clamped to the schema's ranges, from whatever the bag holds. */
export function readRadarSettings(get: (key: string) => unknown): RadarSettings {
  const d = RADAR_DEFAULTS;
  return {
    scale: clampNum(get(RADAR_KEYS.scale), ...RANGES.scale, d.scale),
    hudScale: clampNum(get(RADAR_KEYS.hudScale), ...RANGES.hudScale, d.hudScale),
    iconScale: clampNum(get(RADAR_KEYS.iconScale), ...RANGES.iconScale, d.iconScale),
    centered: asFlag(get(RADAR_KEYS.centered), d.centered),
    rotate: asFlag(get(RADAR_KEYS.rotate), d.rotate),
    fullOnScores: asFlag(get(RADAR_KEYS.fullOnScores), d.fullOnScores),
    soundPings: asFlag(get(RADAR_KEYS.soundPings), d.soundPings),
  };
}

/** What the instrument is looking at this frame. */
export interface RadarFrame {
  /** Cubes across the instrument's diameter. */
  span: number;
  /** The world point under the instrument's centre. */
  cx: number;
  cy: number;
  /** Rotation applied to the world, canvas convention (y down). */
  angle: number;
  /** Rotation of our arrow, which is drawn pointing up before it. */
  arrowAngle: number;
}

/**
 * The view for this frame.
 *
 * - **Span** from `cl_radar_scale`: lower is more map. The zoom key clamps to
 *   `ZOOM_SCALE` while held rather than replacing the setting, so a player who
 *   already plays zoomed out further than that is not zoomed *in* by it.
 * - **Whole map** while the scoreboard is held: centred on the map, north up,
 *   because a picture of the whole map that rotated with you would be the one
 *   view in which rotation buys nothing.
 * - **Centred** or clamped: off, the centre stops half a span from the map's
 *   edge, so you walk off-centre instead of the instrument filling with void.
 * - **Rotated** or north-up: off, the world does not turn and the arrow does.
 */
export function radarFrame(
  s: RadarSettings,
  me: { x: number; y: number; yaw: number },
  ssize: number,
  scoresHeld: boolean,
  zoomHeld: boolean,
): RadarFrame {
  if (scoresHeld && s.fullOnScores) {
    // A circle round the whole square: its diagonal, not its side, or the
    // corners of the map are cut off by the rim.
    const span = ssize * Math.SQRT2;
    return { span, cx: ssize / 2, cy: ssize / 2, angle: 0, arrowAngle: me.yaw + Math.PI / 2 };
  }
  const scale = zoomHeld ? Math.min(s.scale, ZOOM_SCALE) : s.scale;
  const span = (BASE_SPAN * DEFAULT_SCALE) / scale;
  let cx = me.x;
  let cy = me.y;
  if (!s.centered) {
    const half = span / 2;
    cx = half * 2 >= ssize ? ssize / 2 : Math.min(ssize - half, Math.max(half, me.x));
    cy = half * 2 >= ssize ? ssize / 2 : Math.min(ssize - half, Math.max(half, me.y));
  }
  const angle = s.rotate ? -me.yaw - Math.PI / 2 : 0;
  // The arrow points along our yaw *after* the world's rotation, which for the
  // rotated radar is always straight up.
  const arrowAngle = s.rotate ? 0 : me.yaw + Math.PI / 2;
  return { span, cx, cy, angle, arrowAngle };
}

/**
 * A world point as an offset from the instrument's centre, in units where the
 * rim is at `radius`.
 */
export function project(
  frame: RadarFrame,
  radius: number,
  wx: number,
  wy: number,
): [number, number] {
  const perCube = (radius * 2) / frame.span;
  const lx = (wx - frame.cx) * perCube;
  const ly = (wy - frame.cy) * perCube;
  const cos = Math.cos(frame.angle);
  const sin = Math.sin(frame.angle);
  return [lx * cos - ly * sin, lx * sin + ly * cos];
}

export type BlipKind = 'mate' | 'enemy' | 'heard' | 'lastKnown' | 'ping' | 'bomb' | 'you';

/**
 * Blip sizes as a fraction of the radius at the default icon scale. `mate` and
 * `enemy` are the old 3.2 and 3.8 px on the 84 px browser radius, which is the
 * native client's `u * 1.45` and `u * 1.72` on its `u * 38`: the enemy is drawn
 * the larger, because a teammate is context and an enemy is why you looked.
 */
const BLIP_BASE: Record<BlipKind, number> = {
  mate: 3.2 / 84,
  enemy: 3.8 / 84,
  heard: 3.8 / 84,
  lastKnown: 3.4 / 84,
  ping: 5 / 84,
  bomb: 4.6 / 84,
  you: 6 / 84,
};

/** A blip's radius as a fraction of the instrument's, at this icon scale. */
export function blipRadius(kind: BlipKind, iconScale: number): number {
  return BLIP_BASE[kind] * (iconScale / DEFAULT_ICON_SCALE);
}

/**
 * Which brightness band a floor height falls in, `0..FLOOR_BANDS-1`.
 *
 * Banded rather than continuous so the native floor plan can still be merged
 * into runs (a run breaks only where the band changes), and because a radar is
 * read at a glance: "upper level" is information, a gradient is texture.
 */
export function floorBand(floor: number, minFloor: number, maxFloor: number): number {
  if (maxFloor <= minFloor) return Math.floor((FLOOR_BANDS - 1) / 2);
  const t = (floor - minFloor) / (maxFloor - minFloor);
  return Math.max(0, Math.min(FLOOR_BANDS - 1, Math.round(t * (FLOOR_BANDS - 1))));
}

export type Rgba = readonly [number, number, number, number];

/**
 * The radar's palette, 0..1 per channel. In the fixture, so the native client's
 * colours are a test failure rather than a judgement call when they drift.
 *
 * The ground is near-opaque and the floor plan bright enough to read as a map on
 * a dark scene — the old 16% floor over a 72% ground is what made the radar a
 * black disc.
 */
export const RADAR_COLORS = {
  ground: [0.031, 0.043, 0.063, 0.88],
  grid: [0.549, 0.667, 0.824, 0.07],
  floorLow: [0.11, 0.15, 0.21, 0.82],
  floorHigh: [0.47, 0.57, 0.69, 0.92],
  wall: [0.784, 0.863, 0.961, 0.8],
  site: [0.984, 0.749, 0.251, 0.16],
  siteEdge: [0.984, 0.749, 0.251, 0.85],
  you: [0.494, 0.906, 0.529, 1],
  mate: [0.345, 0.651, 1, 0.95],
  enemy: [0.973, 0.318, 0.286, 1],
  soundPing: [0.973, 0.318, 0.286, 0.32],
  ownNoise: [0.784, 0.863, 0.961, 0.3],
  bomb: [1, 0.584, 0.2, 1],
  rim: [0.706, 0.784, 0.902, 0.32],
} as const satisfies Record<string, Rgba>;

/** The floor colour for a band: the two floor ends, mixed. */
export function floorColor(band: number): Rgba {
  const t = FLOOR_BANDS > 1 ? band / (FLOOR_BANDS - 1) : 0;
  const lo = RADAR_COLORS.floorLow;
  const hi = RADAR_COLORS.floorHigh;
  return [
    lo[0] + (hi[0] - lo[0]) * t,
    lo[1] + (hi[1] - lo[1]) * t,
    lo[2] + (hi[2] - lo[2]) * t,
    lo[3] + (hi[3] - lo[3]) * t,
  ];
}

/** One straight piece of wall outline, in cube coordinates. */
export interface Edge {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * Every boundary between an open cell and a solid one, merged into the longest
 * straight segments.
 *
 * This is what turns a floor plan into a map. Filled cells alone read as a
 * blurry blob at radar scale; a crisp line where the floor meets the wall is the
 * thing an eye actually finds a corridor by. Outside the grid counts as solid,
 * so a map open to its own border still has an outline.
 */
export function wallEdges(ssize: number, isSolid: (x: number, y: number) => boolean): Edge[] {
  const open = (x: number, y: number) =>
    x >= 0 && y >= 0 && x < ssize && y < ssize && !isSolid(x, y);
  const out: Edge[] = [];
  // Horizontal lines at y = row boundary: between (x, y-1) and (x, y).
  for (let y = 0; y <= ssize; y += 1) {
    let start = -1;
    for (let x = 0; x <= ssize; x += 1) {
      const edge = x < ssize && open(x, y - 1) !== open(x, y);
      if (edge && start < 0) start = x;
      if (!edge && start >= 0) {
        out.push({ x0: start, y0: y, x1: x, y1: y });
        start = -1;
      }
    }
  }
  // Vertical lines at x = column boundary: between (x-1, y) and (x, y).
  for (let x = 0; x <= ssize; x += 1) {
    let start = -1;
    for (let y = 0; y <= ssize; y += 1) {
      const edge = y < ssize && open(x - 1, y) !== open(x, y);
      if (edge && start < 0) start = y;
      if (!edge && start >= 0) {
        out.push({ x0: x, y0: start, x1: x, y1: y });
        start = -1;
      }
    }
  }
  return out;
}

/** The floor heights of the open cells, low and high. */
export function floorRange(
  ssize: number,
  isSolid: (x: number, y: number) => boolean,
  floorAt: (x: number, y: number) => number,
): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (let y = 0; y < ssize; y += 1) {
    for (let x = 0; x < ssize; x += 1) {
      if (isSolid(x, y)) continue;
      const f = floorAt(x, y);
      if (f < lo) lo = f;
      if (f > hi) hi = f;
    }
  }
  return Number.isFinite(lo) ? [lo, hi] : [0, 0];
}

/** 0..1, fading out over the dot's life; 0 once it is over. */
export function fadeOut(age: number, life: number): number {
  if (!(life > 0)) return 0;
  return Math.max(0, Math.min(1, 1 - age / life));
}

/**
 * The audible radius, in cubes, of our own noises — what the ring round our
 * arrow is sized to. CS2's "how far can I be heard" circle.
 *
 * Copies of `noise.py`'s constants, and pinned against them: the fixture carries
 * this table and `test_hassault_radar_heard.py` reads it back against the
 * Python. A ring that lied about how far you carry would teach the wrong habit.
 */
export const OWN_NOISE_RADII: Readonly<Record<string, number>> = {
  step: 42,
  land: 55,
  jump: 30,
  reload: 34,
  splash: 62,
  pickup: 26,
};

/** `noise.shot_loudness`: a shot's radius from the weapon's damage. */
export function shotRadius(damage: number, knife: boolean, noiseMult = 1): number {
  if (knife) return 8;
  return 120 * Math.min(1, damage / 60 + 0.35) * noiseMult;
}

/** Seconds the own-noise ring takes to fade. */
export const OWN_NOISE_SECONDS = 0.7;
