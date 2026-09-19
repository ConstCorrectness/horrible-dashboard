/**
 * Graphics Quality & Performance Profile.
 *
 * Controls rendering fidelity vs. CPU/GPU workload and battery conservation.
 * Pure CSS properties on `html[data-graphics-quality='...']` drive blur removal,
 * animation throttling, and simplified shadows without component re-renders, while
 * reactive hooks let WebGL/canvas scenes (3D avatars, game loops) scale back particles
 * and frame rates on weaker hardware.
 *
 * Automatically defaults to 'performance' on low-spec hardware (<=4 cores, <=4GB RAM,
 * mobile devices, or when OS reduced-motion / reduced-transparency is enabled).
 */
import { getSetting, settingsStore, useSetting } from './settings';

export type GraphicsQuality = 'performance' | 'balanced' | 'quality';

export const GRAPHICS_QUALITY_SETTING_KEY = 'settings.graphicsQuality';
export const GRAPHICS_QUALITIES: readonly GraphicsQuality[] = [
  'performance',
  'balanced',
  'quality',
];

/** Auto-detect whether the current device is resource-constrained. */
export function detectDefaultGraphicsQuality(): GraphicsQuality {
  if (typeof window === 'undefined') return 'balanced';
  try {
    if (
      window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ||
      window.matchMedia?.('(prefers-reduced-transparency: reduce)')?.matches
    ) {
      return 'performance';
    }
    const cores = navigator.hardwareConcurrency;
    if (cores && cores <= 4) return 'performance';

    const mem = (navigator as unknown as { deviceMemory?: number }).deviceMemory;
    if (mem && mem <= 4) return 'performance';

    if (
      /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
    ) {
      return 'performance';
    }
  } catch {
    // Fallback if APIs are restricted
  }
  return 'balanced';
}

export function isKnownGraphicsQuality(val: unknown): val is GraphicsQuality {
  return typeof val === 'string' && GRAPHICS_QUALITIES.includes(val as GraphicsQuality);
}

export function currentGraphicsQuality(): GraphicsQuality {
  const q = getSetting<string>(GRAPHICS_QUALITY_SETTING_KEY);
  return q && isKnownGraphicsQuality(q) ? q : detectDefaultGraphicsQuality();
}

export function applyGraphicsQuality(quality: GraphicsQuality): void {
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.dataset.graphicsQuality = quality;
  }
}

export function initGraphicsQuality(): () => void {
  applyGraphicsQuality(currentGraphicsQuality());
  return settingsStore.subscribe(() => applyGraphicsQuality(currentGraphicsQuality()));
}

export function useGraphicsQuality(): GraphicsQuality {
  const q = useSetting<string>(GRAPHICS_QUALITY_SETTING_KEY);
  return q && isKnownGraphicsQuality(q) ? q : detectDefaultGraphicsQuality();
}
