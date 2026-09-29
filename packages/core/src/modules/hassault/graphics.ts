/**
 * The pane's graphics tier: how much of the served map look the browser draws.
 *
 * The browser is not the native client and does not try to be: no post chain, no
 * MSAA sample counts, no GPU choice. What it gets is one setting with three
 * honest levels — and the **same map data** at every one of them: the same sky
 * colours, sun, fog and lamps as the native window, drawn with fewer of them at
 * the lower levels, never with different ones.
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
  /** Multiplier on the map's fog density — Low hides distance to save fill. */
  fogScale: number;
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
    fogScale: 2,
  },
  medium: {
    pixelRatio: 1.5,
    antialias: true,
    shadowSize: 2048,
    softShadows: false,
    lights: 4,
    sky: true,
    anisotropy: 4,
    fogScale: 0.0075 / 0.0055,
  },
  high: {
    pixelRatio: 2,
    antialias: true,
    shadowSize: 2048,
    softShadows: true,
    lights: 8,
    sky: true,
    anisotropy: 16,
    fogScale: 1,
  },
};

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
  if (/intel|uhd|iris|adreno|mali|apple|radeon\(tm\) graphics|vega/.test(r)) return 'medium';
  // Unknown (the renderer string is masked): a very dense display is usually a
  // laptop or a phone, where fill rate is the constraint.
  return hints.devicePixelRatio >= 2.5 ? 'medium' : 'high';
}

export function resolveTier(choice: GraphicsChoice, hints: DeviceHints): GraphicsTier {
  return choice === 'auto' ? autoTier(hints) : choice;
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
const listeners = new Set<() => void>();

export function configureGraphicsStorage(standalone: boolean): void {
  if (standalone === localMode) return;
  localMode = standalone;
  if (standalone) localChoice = readLocal();
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
    return `${localMode ? "local" : "node"}:${localChoice}`;
  },
};

export function setGraphicsChoice(choice: GraphicsChoice): void {
  if (localMode) {
    writeLocal(choice);
    localChoice = choice;
    for (const l of listeners) l();
  } else {
    void setSetting(GRAPHICS_KEY, choice).catch(() => {});
  }
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
