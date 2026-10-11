/**
 * The pane's graphics tier: how much of the served map look the browser draws.
 *
 * The browser is not the native client and does not try to be: no post chain, no
 * MSAA sample counts, no GPU choice. What it gets is three honest levels — and the
 * **same map data** at every one of them: the same sky colours, sun, fog and lamps
 * as the native window, drawn with fewer of them at the lower levels, never with
 * different ones.
 *
 * ## A level is a preset, and every row is its own setting
 *
 * As natively (`Video::apply_preset`): picking a level sets every row, and a row
 * changed afterwards is kept as an **override** on top of it, so the menu shows
 * `Custom` rather than pretending the level still describes the picture. Picking
 * a level again clears the overrides — a preset that kept somebody's old tweak
 * underneath would be a preset that does not do what it says. The overrides are
 * one JSON string under `GRAPHICS_CUSTOM_KEY`, the shape the controls map uses,
 * because the settings page renders scalars and this is a document.
 *
 * ## What a level may not change
 *
 * Anything that decides **what a player can see** is not a graphics setting:
 * the fog's density, smoke and flash opacity, which props are drawn, how a
 * player is lit. Low used to double the fog to save fill rate, which meant a
 * player on a weak laptop could not see somebody a player on a good one could —
 * a setting that loses gunfights. Fill rate comes out of the pixel ratio, the
 * lamps' shading cost and the shadow filter instead. The native client keeps the
 * same rule (`settings.rs`, `Quality`), and `__tests__/atmosphere.test.ts` and
 * `browser_parity.rs` hold both to it.
 *
 * `auto` picks from what the page can see: a phone-sized pixel ratio or a
 * software rasteriser gets Low, an integrated GPU gets Medium, the rest High.
 *
 * Stored in the node's settings bag like every other preference — except in the
 * standalone web build, which has no node and whose game server serves no
 * `/api/settings`. There it lives in `localStorage`, wrapped, because private
 * windows and blocked storage throw.
 */
import { useSyncExternalStore } from 'react';

import { setSetting, useSetting } from '../../settings';

export const GRAPHICS_KEY = 'hassault.graphics';

export type GraphicsChoice = 'auto' | 'low' | 'medium' | 'high';
export type GraphicsTier = Exclude<GraphicsChoice, 'auto'>;

export const GRAPHICS_CHOICES: readonly GraphicsChoice[] = ['auto', 'low', 'medium', 'high'];

export interface TierSpec {
  /** Cap on `devicePixelRatio`: the single largest cost on a HiDPI laptop. */
  pixelRatio: number;
  /** The context's own MSAA. A creation flag, so it applies on the next load. */
  antialias: boolean;
  /** The sun's shadow map edge. High is the old fixed 2048. */
  shadowSize: number;
  /** `PCFSoftShadowMap` rather than `PCFShadowMap`. */
  softShadows: boolean;
  /** How many of the map's point lights shade a frame, nearest first. */
  lights: number;
  /** The sky dome on maps open to the sky; off is the flat horizon colour. */
  sky: boolean;
  /** Anisotropic filtering on the surface tiles, capped by the GPU's maximum. */
  anisotropy: number;
  /** Map surfaces as Lambert instead of PBR (see surface-quality.ts). The
   * single biggest fill saving on an integrated GPU. */
  lambert: boolean;
  /** The photographed CC0 maps on the surfaces that have them, in place of the
   * generated tiles. A download and some VRAM; meaningless under Lambert, which
   * has no normal or roughness to put them in. */
  photos: boolean;
}

/**
 * The levels. **High is the native client's High** for everything the two share:
 * the map's own fog, a 2048 shadow map, eight lights is what the native Medium
 * shades and High draws sixteen — the browser pays per light in shader cost, and
 * eight nearest is the knee of that curve.
 */
export const TIERS: Record<GraphicsTier, TierSpec> = {
  low: {
    pixelRatio: 1,
    antialias: false,
    shadowSize: 1024,
    softShadows: false,
    lights: 0,
    sky: false,
    anisotropy: 1,
    lambert: true,
    photos: false,
  },
  medium: {
    pixelRatio: 1.5,
    antialias: true,
    shadowSize: 2048,
    softShadows: false,
    lights: 4,
    sky: true,
    anisotropy: 4,
    lambert: false,
    photos: true,
  },
  high: {
    pixelRatio: 2,
    antialias: true,
    shadowSize: 2048,
    softShadows: true,
    lights: 8,
    sky: true,
    anisotropy: 16,
    lambert: false,
    photos: true,
  },
};

/** Every row's override, all optional: what differs from the level. */
export type GraphicsOverrides = Partial<TierSpec>;

export const GRAPHICS_CUSTOM_KEY = 'hassault.graphics.custom';

/**
 * The values each numeric row offers. Lists, not ranges: these are the steps
 * that are worth telling apart, and a free number would invite a 7000-px shadow
 * map. `lights` tops out at the size of the scene's fixed lamp pool.
 */
export const GRAPHICS_OPTIONS = {
  pixelRatio: [1, 1.5, 2, 3],
  shadowSize: [1024, 2048, 4096],
  lights: [0, 2, 4, 8],
  anisotropy: [1, 4, 8, 16],
} as const satisfies Partial<Record<keyof TierSpec, readonly number[]>>;

/** The on/off rows. */
export const GRAPHICS_TOGGLES = ['antialias', 'softShadows', 'sky', 'lambert', 'photos'] as const;

/** The lamp pool's size: the most any row can ask for. */
export const MAX_LIGHTS = Math.max(...GRAPHICS_OPTIONS.lights);

/**
 * Overrides out of storage. Anything not a known row, or not one of that row's
 * values, is dropped rather than trusted — this is a string somebody can edit.
 */
export function parseOverrides(raw: unknown): GraphicsOverrides {
  let value: unknown = raw;
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw);
    } catch {
      return {};
    }
  }
  if (!value || typeof value !== 'object') return {};
  const source = value as Record<string, unknown>;
  const out: GraphicsOverrides = {};
  for (const [key, options] of Object.entries(GRAPHICS_OPTIONS) as [
    keyof typeof GRAPHICS_OPTIONS,
    readonly number[],
  ][]) {
    const v = source[key];
    if (typeof v === 'number' && options.includes(v)) out[key] = v;
  }
  for (const key of GRAPHICS_TOGGLES) {
    const v = source[key];
    if (typeof v === 'boolean') out[key] = v;
  }
  return out;
}

/** A level with the player's overrides on top: what the scene draws. */
export function effectiveSpec(tier: GraphicsTier, overrides: GraphicsOverrides): TierSpec {
  return { ...TIERS[tier], ...overrides };
}

/**
 * Set one row, dropping it from the overrides when it equals the level's own
 * value — so moving a row back to where the level had it returns the menu from
 * `Custom` to the level's name, as it does natively.
 */
export function withOverride<K extends keyof TierSpec>(
  overrides: GraphicsOverrides,
  tier: GraphicsTier,
  key: K,
  value: TierSpec[K],
): GraphicsOverrides {
  const next: GraphicsOverrides = { ...overrides };
  if (TIERS[tier][key] === value) delete next[key];
  else next[key] = value;
  return next;
}

export function isGraphicsChoice(v: unknown): v is GraphicsChoice {
  return typeof v === 'string' && (GRAPHICS_CHOICES as readonly string[]).includes(v);
}

/** What the page can tell about the machine, for `auto`. */
export interface DeviceHints {
  devicePixelRatio: number;
  /** `WEBGL_debug_renderer_info`'s unmasked renderer, when the browser gives it. */
  renderer?: string;
  /** `navigator.hardwareConcurrency`. */
  cores?: number;
}

/** `auto`, decided. Conservative: a wrong Low is a setting, a wrong High is a slideshow. */
export function autoTier(hints: DeviceHints): GraphicsTier {
  const r = (hints.renderer ?? '').toLowerCase();
  if (/swiftshader|llvmpipe|software|basic render/.test(r)) return 'low';
  if ((hints.cores ?? 8) <= 2) return 'low';
  if (/nvidia|geforce|rtx|radeon rx|radeon pro|arc a|apple m[1-9] (pro|max|ultra)/.test(r)) {
    return 'high';
  }
  // The entry-level integrated parts go Low. Measured on an Intel UHD at 1080p,
  // Dust II: Medium (four lamps, PBR surfaces) 43 fps, Low 162 — and Low looks
  // nearly the same, because the lamps and the PBR term are what it drops.
  if (/uhd|intel\(r\) hd|mali|adreno/.test(r)) return 'low';
  if (/intel|iris|apple|radeon\(tm\) graphics|vega/.test(r)) return 'medium';
  // Unknown (the renderer string is masked): a very dense display is usually a
  // laptop or a phone, where fill rate is the constraint.
  return hints.devicePixelRatio >= 2.5 ? 'medium' : 'high';
}

export function resolveTier(choice: GraphicsChoice, hints: DeviceHints): GraphicsTier {
  return choice === 'auto' ? autoTier(hints) : choice;
}

let seenHints: DeviceHints | null = null;

/** Remember what the scene's context said about the machine, so the settings
 * rows resolve `auto` to the level the scene is actually drawing. */
export function noteDeviceHints(hints: DeviceHints): void {
  seenHints = hints;
}

/** The scene's hints, or what the page alone can tell before a scene exists. */
export function deviceHints(): DeviceHints {
  return (
    seenHints ?? {
      devicePixelRatio: globalThis.devicePixelRatio || 1,
      cores: globalThis.navigator?.hardwareConcurrency,
    }
  );
}

/** The unmasked renderer string of a WebGL context, if the browser exposes it. */
export function rendererName(gl: WebGLRenderingContext | WebGL2RenderingContext): string {
  try {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    if (ext) return String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) ?? '');
    return String(gl.getParameter(gl.RENDERER) ?? '');
  } catch {
    return '';
  }
}

const STORAGE_KEY = 'hassault.graphics';
const CUSTOM_STORAGE_KEY = GRAPHICS_CUSTOM_KEY;

function readLocalOverrides(): GraphicsOverrides {
  try {
    return parseOverrides(globalThis.localStorage?.getItem(CUSTOM_STORAGE_KEY) ?? '');
  } catch {
    return {};
  }
}

function writeLocalOverrides(overrides: GraphicsOverrides): void {
  try {
    globalThis.localStorage?.setItem(CUSTOM_STORAGE_KEY, JSON.stringify(overrides));
  } catch {
    // Storage blocked: kept for this page only.
  }
}

function readLocal(): GraphicsChoice {
  try {
    const v = globalThis.localStorage?.getItem(STORAGE_KEY);
    return isGraphicsChoice(v) ? v : 'auto';
  } catch {
    return 'auto';
  }
}

function writeLocal(choice: GraphicsChoice): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, choice);
  } catch {
    // Storage blocked: the choice holds for this page and is simply not kept.
  }
}

/**
 * Where the choice lives in this page. The standalone web build has no node, so
 * the panel that knows it is standalone says so once, and every reader after —
 * the scene, and the settings rows in both menus — shares one local store.
 */
let localMode = false;
let localChoice: GraphicsChoice = "auto";
let localOverrides: GraphicsOverrides = {};
const listeners = new Set<() => void>();

export function configureGraphicsStorage(standalone: boolean): void {
  if (standalone === localMode) return;
  localMode = standalone;
  if (standalone) {
    localChoice = readLocal();
    localOverrides = readLocalOverrides();
  }
  for (const l of listeners) l();
}

const localStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  snapshot(): string {
    return `${localMode ? "local" : "node"}:${localChoice}:${JSON.stringify(localOverrides)}`;
  },
};

/** Pick a level. A preset writes every row, so it clears the overrides. */
export function setGraphicsChoice(choice: GraphicsChoice): void {
  if (localMode) {
    writeLocal(choice);
    writeLocalOverrides({});
    localChoice = choice;
    localOverrides = {};
    for (const l of listeners) l();
  } else {
    void setSetting(GRAPHICS_KEY, choice).catch(() => {});
    void setSetting(GRAPHICS_CUSTOM_KEY, '{}').catch(() => {});
  }
}

/** Replace the overrides, leaving the level where it is. */
export function setGraphicsOverrides(overrides: GraphicsOverrides): void {
  const clean = parseOverrides(overrides);
  if (localMode) {
    writeLocalOverrides(clean);
    localOverrides = clean;
    for (const l of listeners) l();
  } else {
    void setSetting(GRAPHICS_CUSTOM_KEY, JSON.stringify(clean)).catch(() => {});
  }
}

/** The player's overrides, from the same store as the level. */
export function useGraphicsOverrides(): GraphicsOverrides {
  const stored = useSetting<string>(GRAPHICS_CUSTOM_KEY);
  useSyncExternalStore(localStore.subscribe, localStore.snapshot);
  return localMode ? localOverrides : parseOverrides(stored ?? '');
}

/**
 * The player's choice and a setter, from wherever this build keeps it: the
 * node's settings bag, or — standalone — the page's own storage.
 */
export function useGraphicsChoice(): [
  GraphicsChoice,
  (c: GraphicsChoice) => void,
] {
  const stored = useSetting<string>(GRAPHICS_KEY);
  useSyncExternalStore(localStore.subscribe, localStore.snapshot);
  const choice = localMode
    ? localChoice
    : isGraphicsChoice(stored)
      ? stored
      : "auto";
  return [choice, setGraphicsChoice];
}
