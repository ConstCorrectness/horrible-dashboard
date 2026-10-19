/**
 * The crosshair: spec v1, its `HXC1-` share code, and the shapes it draws.
 *
 * A port of `backend/modules/hassault/crosshair.py`, which is the reference.
 * `apps/native-fps/src/crosshair.rs` is the other port, and
 * `__tests__/crosshair-vectors.json` — written from the Python by
 * `scripts/gen_hassault_crosshair_vectors.py` — is replayed by all three suites.
 * That fixture is what makes a code pasted into either client draw the same
 * rectangles; if `crosshair.test.ts` fails after a change here, the change
 * belongs in the Python first.
 *
 * Every quantization is `Math.floor(x * k + 0.5)`, never `Math.round` alone, to
 * match the reference exactly. Hit and kill feedback are not here: a hitmarker is
 * its own overlay and never recolours the reticle.
 */

export const STYLES = ['cross', 'crossDot', 'dot', 'circle'] as const;
export type CrosshairStyle = (typeof STYLES)[number];

export const NAMED_COLORS = {
  white: [235, 240, 245],
  green: [115, 242, 115],
  cyan: [102, 230, 242],
  amber: [247, 199, 89],
  magenta: [242, 115, 230],
  red: [247, 89, 82],
} as const satisfies Record<string, readonly [number, number, number]>;
export type ColorName = keyof typeof NAMED_COLORS;
export const COLOR_NAMES = Object.keys(NAMED_COLORS) as ColorName[];
export const CUSTOM = 'custom';

export const LUMA_FLOOR = 90;
const LUMA_WEIGHTS = [2126, 7152, 722] as const;

/** `[lowest, highest, quanta per unit]`, in quanta: a value is `q / k`. */
type Range = readonly [number, number, number];
export const SIZE: Range = [2, 24, 2];
export const GAP: Range = [-10, 40, 2];
export const THICKNESS: Range = [2, 30, 10];
export const OUTLINE: Range = [0, 20, 10];
export const ALPHA: Range = [15, 100, 100];

export const OUTLINE_ALPHA = 0.85;
export const PIP_ALPHA = 0.45;
export const CONE_PX_PER_RAD = 130;

export type Rgb = [number, number, number];

export interface CrosshairSpec {
  style: CrosshairStyle;
  size: number;
  /** Signed: below zero the arms cross the centre. */
  gap: number;
  thickness: number;
  /** Outline width in HUD units; 0 is no outline. */
  outline: number;
  alpha: number;
  dot: boolean;
  /** Drop the top arm. */
  tStyle: boolean;
  /** Open with the weapon's cone. */
  dynamic: boolean;
  color: ColorName | typeof CUSTOM;
  rgb: Rgb;
}

export const DEFAULT_SPEC: CrosshairSpec = {
  style: 'cross',
  size: 3,
  gap: 4,
  thickness: 0.6,
  outline: 0.3,
  alpha: 0.9,
  dot: true,
  tStyle: false,
  dynamic: true,
  color: 'white',
  rgb: [...NAMED_COLORS.white],
};

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function q(value: unknown, [lo, hi, k]: Range): number {
  if (!isNum(value)) return lo;
  return Math.max(lo, Math.min(hi, Math.floor(value * k + 0.5)));
}

function fromQ(n: number, range: Range): number {
  return n / range[2];
}

function byte(v: unknown): number {
  if (!isNum(v)) return 0;
  return Math.max(0, Math.min(255, Math.floor(v + 0.5)));
}

function isColorName(c: unknown): c is ColorName {
  return typeof c === 'string' && Object.prototype.hasOwnProperty.call(NAMED_COLORS, c);
}

/** Lift a colour towards white until its luma reaches `LUMA_FLOOR`; integer maths. */
export function liftLuma(rgb: Rgb): Rgb {
  const luma = LUMA_WEIGHTS[0] * rgb[0] + LUMA_WEIGHTS[1] * rgb[1] + LUMA_WEIGHTS[2] * rgb[2];
  const floor = LUMA_FLOOR * 10000;
  if (luma >= floor) return rgb;
  const need = floor - luma;
  const room = 255 * 10000 - luma;
  // ceil((255 - c) * need / room); every term is an integer below 2^53.
  return rgb.map((c) => c + Math.ceil(((255 - c) * need) / room)) as Rgb;
}

export function normalizeSpec(spec: Partial<CrosshairSpec>): CrosshairSpec {
  const style = (STYLES as readonly string[]).includes(spec.style as string)
    ? (spec.style as CrosshairStyle)
    : 'cross';
  let color: CrosshairSpec['color'];
  let rgb: Rgb;
  if (isColorName(spec.color)) {
    color = spec.color;
    rgb = [...NAMED_COLORS[spec.color]];
  } else if (spec.color === CUSTOM) {
    color = CUSTOM;
    const raw = Array.isArray(spec.rgb) ? spec.rgb : [];
    rgb = liftLuma([byte(raw[0]), byte(raw[1]), byte(raw[2])]);
  } else {
    color = 'white';
    rgb = [...NAMED_COLORS.white];
  }
  return {
    style,
    size: fromQ(q(spec.size, SIZE), SIZE),
    gap: fromQ(q(spec.gap, GAP), GAP),
    thickness: fromQ(q(spec.thickness, THICKNESS), THICKNESS),
    outline: fromQ(q(spec.outline, OUTLINE), OUTLINE),
    alpha: fromQ(q(spec.alpha, ALPHA), ALPHA),
    dot: Boolean(spec.dot),
    tStyle: Boolean(spec.tStyle),
    dynamic: Boolean(spec.dynamic),
    color,
    rgb,
  };
}

// ---- settings bag -----------------------------------------------------------

export const CROSSHAIR_KEYS = {
  style: 'hassault.crosshair.style',
  size: 'hassault.crosshair.size',
  gap: 'hassault.crosshair.gap',
  thickness: 'hassault.crosshair.thickness',
  outline: 'hassault.crosshair.outline',
  outlineThickness: 'hassault.crosshair.outlineThickness',
  alpha: 'hassault.crosshair.alpha',
  dot: 'hassault.crosshair.dot',
  tStyle: 'hassault.crosshair.tStyle',
  dynamic: 'hassault.crosshair.dynamic',
  color: 'hassault.crosshair.color',
} as const;

/** `#rrggbb` as bytes, or null. Strict: no shorthand, no names, no spaces. */
export function hexRgb(text: string): Rgb | null {
  const t = text.trim();
  if (!/^#[0-9a-fA-F]{6}$/.test(t)) return null;
  return [parseInt(t.slice(1, 3), 16), parseInt(t.slice(3, 5), 16), parseInt(t.slice(5, 7), 16)];
}

export function rgbHex(rgb: Rgb): string {
  return '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('');
}

type Bag = Record<string, unknown>;

/** The spec the settings bag describes, normalized; per key, a bad row is its default. */
export function specFromSettings(bag: Bag): CrosshairSpec {
  const d = DEFAULT_SPEC;
  const num = (k: keyof typeof CROSSHAIR_KEYS, def: number) => {
    const v = bag[CROSSHAIR_KEYS[k]];
    return typeof v === 'number' ? v : def;
  };
  const flag = (k: keyof typeof CROSSHAIR_KEYS, def: boolean) => {
    const v = bag[CROSSHAIR_KEYS[k]];
    return typeof v === 'boolean' ? v : def;
  };
  const text = (k: keyof typeof CROSSHAIR_KEYS, def: string) => {
    const v = bag[CROSSHAIR_KEYS[k]];
    return typeof v === 'string' ? v : def;
  };
  const colorText = text('color', d.color);
  const rgb = hexRgb(colorText);
  return normalizeSpec({
    style: text('style', d.style) as CrosshairStyle,
    size: num('size', d.size),
    gap: num('gap', d.gap),
    thickness: num('thickness', d.thickness),
    outline: flag('outline', true) ? num('outlineThickness', d.outline) : 0,
    alpha: num('alpha', d.alpha),
    dot: flag('dot', d.dot),
    tStyle: flag('tStyle', d.tStyle),
    dynamic: flag('dynamic', d.dynamic),
    color: (rgb ? CUSTOM : colorText) as CrosshairSpec['color'],
    rgb: rgb ?? d.rgb,
  });
}

/** The rows to write for `spec`; an outline of 0 leaves the width row alone. */
export function settingsFromSpec(spec: CrosshairSpec): Record<string, string | number | boolean> {
  const s = normalizeSpec(spec);
  const rows: Record<string, string | number | boolean> = {
    [CROSSHAIR_KEYS.style]: s.style,
    [CROSSHAIR_KEYS.size]: s.size,
    [CROSSHAIR_KEYS.gap]: s.gap,
    [CROSSHAIR_KEYS.thickness]: s.thickness,
    [CROSSHAIR_KEYS.outline]: s.outline > 0,
    [CROSSHAIR_KEYS.alpha]: s.alpha,
    [CROSSHAIR_KEYS.dot]: s.dot,
    [CROSSHAIR_KEYS.tStyle]: s.tStyle,
    [CROSSHAIR_KEYS.dynamic]: s.dynamic,
    [CROSSHAIR_KEYS.color]: s.color === CUSTOM ? rgbHex(s.rgb) : s.color,
  };
  if (s.outline > 0) rows[CROSSHAIR_KEYS.outlineThickness] = s.outline;
  return rows;
}

// ---- share code -------------------------------------------------------------

export const PREFIX = 'HXC1-';
export const VERSION = 1;
const FIELDS = 11;
const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
const DECODE: Record<string, number> = { O: 0, I: 1, L: 1 };
for (let i = 0; i < ALPHABET.length; i++) DECODE[ALPHABET[i]] = i;

export type DecodeFailure = 'prefix' | 'char' | 'short' | 'crc' | 'version';
export type DecodeResult = { ok: true; spec: CrosshairSpec } | { ok: false; reason: DecodeFailure };

export function crc8(data: ArrayLike<number>): number {
  let crc = 0;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let b = 0; b < 8; b++) crc = crc & 0x80 ? ((crc << 1) ^ 0x07) & 0xff : (crc << 1) & 0xff;
  }
  return crc;
}

export function packSpec(spec: CrosshairSpec): Uint8Array {
  const s = normalizeSpec(spec);
  const flags = (s.dot ? 1 : 0) | (s.tStyle ? 2 : 0) | (s.dynamic ? 4 : 0);
  const color = s.color === CUSTOM ? COLOR_NAMES.length : COLOR_NAMES.indexOf(s.color);
  const rgb = s.color === CUSTOM ? s.rgb : [0, 0, 0];
  const body = [
    VERSION,
    STYLES.indexOf(s.style),
    flags,
    q(s.size, SIZE),
    q(s.gap, GAP) & 0xff,
    q(s.thickness, THICKNESS),
    q(s.outline, OUTLINE),
    q(s.alpha, ALPHA),
    color,
    ...rgb,
  ];
  return Uint8Array.from([...body, crc8(body)]);
}

export function unpackSpec(payload: Uint8Array): DecodeResult {
  if (payload.length < 2 + FIELDS) return { ok: false, reason: 'short' };
  if (crc8(payload.subarray(0, payload.length - 1)) !== payload[payload.length - 1]) {
    return { ok: false, reason: 'crc' };
  }
  if (payload[0] !== VERSION) return { ok: false, reason: 'version' };
  const f = payload.subarray(1, 1 + FIELDS);
  const gap = f[3] >= 128 ? f[3] - 256 : f[3];
  let color: CrosshairSpec['color'] = 'white';
  let rgb: Rgb = [...NAMED_COLORS.white];
  if (f[7] < COLOR_NAMES.length) {
    color = COLOR_NAMES[f[7]];
    rgb = [...NAMED_COLORS[color]];
  } else if (f[7] === COLOR_NAMES.length) {
    color = CUSTOM;
    rgb = [f[8], f[9], f[10]];
  }
  return {
    ok: true,
    spec: normalizeSpec({
      style: f[0] < STYLES.length ? STYLES[f[0]] : 'cross',
      size: fromQ(f[2], SIZE),
      gap: fromQ(gap, GAP),
      thickness: fromQ(f[4], THICKNESS),
      outline: fromQ(f[5], OUTLINE),
      alpha: fromQ(f[6], ALPHA),
      dot: Boolean(f[1] & 1),
      tStyle: Boolean(f[1] & 2),
      dynamic: Boolean(f[1] & 4),
      color,
      rgb,
    }),
  };
}

function base32(data: ArrayLike<number>): string {
  let out = '';
  let acc = 0;
  let bits = 0;
  for (let i = 0; i < data.length; i++) {
    acc = ((acc << 8) | data[i]) & 0xffff;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      out += ALPHABET[(acc >> bits) & 31];
    }
  }
  if (bits) out += ALPHABET[(acc << (5 - bits)) & 31];
  return out;
}

function unbase32(text: string): Uint8Array | null {
  const out: number[] = [];
  let acc = 0;
  let bits = 0;
  for (const ch of text) {
    const v = DECODE[ch];
    if (v === undefined) return null;
    acc = ((acc << 5) | v) & 0xffff;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((acc >> bits) & 0xff);
    }
  }
  return Uint8Array.from(out);
}

export function encodePayload(payload: ArrayLike<number>): string {
  const chars = base32(payload);
  const groups: string[] = [];
  for (let i = 0; i < chars.length; i += 5) groups.push(chars.slice(i, i + 5));
  return PREFIX + groups.join('-');
}

export function encodeCrosshair(spec: CrosshairSpec): string {
  return encodePayload(packSpec(spec));
}

/** Forgiving about what a paste does to a code; strict about what it draws. */
export function decodeCrosshair(code: string): DecodeResult {
  const t = code.trim().toUpperCase();
  if (!t.startsWith(PREFIX)) return { ok: false, reason: 'prefix' };
  const body = t.slice(PREFIX.length).replace(/[- ]/g, '');
  const payload = unbase32(body);
  if (!payload) return { ok: false, reason: 'char' };
  return unpackSpec(payload);
}

// ---- geometry ---------------------------------------------------------------

/** Pixels per HUD unit: whole pixels per 360 of height, times the HUD scale. */
export function hudUnit(heightPx: number, hudScale: number): number {
  const scale = Math.min(1.5, Math.max(0.75, isNum(hudScale) ? hudScale : 1));
  return Math.max(2, Math.floor(heightPx / 360 + 0.5) * scale);
}

/** How far a cone of `coneRad` opens the crosshair, in pixels. */
export function coneGapPx(coneRad: number, unit: number): number {
  return Math.max(0, coneRad) * CONE_PX_PER_RAD * unit;
}

export type Layer = 'outline' | 'fill';
export type CrosshairShape =
  | { kind: 'rect'; layer: Layer; alpha: number; x: number; y: number; w: number; h: number }
  | { kind: 'ring'; layer: Layer; alpha: number; r0: number; r1: number };

type Element =
  | { kind: 'rect'; g: [number, number, number, number]; frac: number }
  | { kind: 'ring'; g: [number, number]; frac: number };

/** The shapes to draw, around the centre (y down), outline layer first. */
export function crosshairRects(
  spec: CrosshairSpec,
  coneGap: number,
  unit: number,
): CrosshairShape[] {
  const s = normalizeSpec(spec);
  const half = Math.max(s.thickness * unit, 0.35);
  const arm = s.size * unit;
  const gap = s.gap * unit * 0.5 + (s.dynamic ? Math.max(0, coneGap) : 0);
  const out = s.outline > 0 ? Math.max(s.outline * unit, 1) : 0;

  const elements: Element[] = [];
  const square = (r: number, frac = 1) =>
    elements.push({ kind: 'rect', g: [-r, -r, r * 2, r * 2], frac });
  const arms = () => {
    elements.push({ kind: 'rect', g: [-gap - arm, -half, arm, half * 2], frac: 1 });
    elements.push({ kind: 'rect', g: [gap, -half, arm, half * 2], frac: 1 });
    if (!s.tStyle) elements.push({ kind: 'rect', g: [-half, -gap - arm, half * 2, arm], frac: 1 });
    elements.push({ kind: 'rect', g: [-half, gap, half * 2, arm], frac: 1 });
  };

  if (s.style === 'cross') {
    arms();
    if (s.dot) square(half);
  } else if (s.style === 'crossDot') {
    arms();
    square(half * 2, PIP_ALPHA);
    if (s.dot) square(half);
  } else if (s.style === 'dot') {
    square(half);
  } else {
    const inner = Math.max(gap, 1);
    elements.push({ kind: 'ring', g: [inner, inner + half * 2], frac: 1 });
    if (s.dot) square(half);
  }

  const shapes: CrosshairShape[] = [];
  if (out > 0) {
    for (const e of elements) {
      const alpha = OUTLINE_ALPHA * s.alpha * e.frac;
      if (e.kind === 'rect') {
        const [x, y, w, h] = e.g;
        shapes.push({
          kind: 'rect',
          layer: 'outline',
          alpha,
          x: x - out,
          y: y - out,
          w: w + out * 2,
          h: h + out * 2,
        });
      } else {
        shapes.push({
          kind: 'ring',
          layer: 'outline',
          alpha,
          r0: Math.max(0, e.g[0] - out),
          r1: e.g[1] + out,
        });
      }
    }
  }
  for (const e of elements) {
    const alpha = s.alpha * e.frac;
    if (e.kind === 'rect') {
      const [x, y, w, h] = e.g;
      shapes.push({ kind: 'rect', layer: 'fill', alpha, x, y, w, h });
    } else {
      shapes.push({ kind: 'ring', layer: 'fill', alpha, r0: e.g[0], r1: e.g[1] });
    }
  }
  return shapes;
}

/** Starting points for the generator page, as share codes (pinned by the fixture). */
export const PRESETS: Record<string, string> = {
  default: 'HXC1-0400A-1G80R-1NM00-00008-P',
  classic: 'HXC1-04008-2040M-1NM08-00009-E',
  tight: 'HXC1-04000-17Y0G-05M0G-00000-G',
  't-cross': 'HXC1-0400E-2G60M-1NM0R-0000D-E',
  ring: 'HXC1-041GA-1GC0G-1NM10-00000-E',
  dot: 'HXC1-0410A-1G818-26818-0000B-Y',
};
