/**
 * Where each view was last left, so it opens back there.
 *
 * Dragging the terminal into the left dock is a decision about where that pane
 * *belongs*, and it should outlive closing it — which the frame alone cannot do,
 * because a closed pane is not in the frame to remember anything with.
 *
 * Keyed by **view id, not pane instance**: instance ids are minted per open, so
 * keying by one would remember a placement that can never be looked up again.
 *
 * Global rather than per-workspace, and a setting rather than the layout blob —
 * where you keep a tool is a habit, not a layout. It rides
 * `frame.panePlacement` as one JSON string, so it hydrates at boot and persists
 * server-side for free.
 *
 * The floating desktop kept a rect and a snap zone here (`frame.windowPlacement`).
 * A rect means nothing to a workbench, so that setting is simply no longer read.
 */
import { getSetting, setSetting } from '../settings';
import { DOCK_SIDES } from './dock/types';
import type { DockSide } from './dock/types';

export const PANE_PLACEMENT_KEY = 'frame.panePlacement';

/** An edge dock, or the centre. */
export type PanePlacement = DockSide | 'center';

function isPlacement(value: unknown): value is PanePlacement {
  return value === 'center' || (DOCK_SIDES as readonly unknown[]).includes(value);
}

/** Tolerant parse — a malformed blob degrades to "remember nothing", never throws. */
function parse(raw: string | undefined): Record<string, PanePlacement> {
  if (!raw) return {};
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null;
    if (!data || typeof data !== 'object') return {};
    const out: Record<string, PanePlacement> = {};
    for (const [viewId, value] of Object.entries(data)) {
      if (isPlacement(value)) out[viewId] = value;
    }
    return out;
  } catch {
    return {};
  }
}

// Parsed once per distinct raw string: `lastPlacement` is called on every open.
let cache: { raw: string | undefined; value: Record<string, PanePlacement> } | null = null;

function all(): Record<string, PanePlacement> {
  const raw = getSetting<string>(PANE_PLACEMENT_KEY);
  if (!cache || cache.raw !== raw) cache = { raw, value: parse(raw) };
  return cache.value;
}

/**
 * Placements recorded but not yet written. Read before the setting, so a view
 * reopened inside the debounce window still finds where it was just left.
 */
const pending = new Map<string, PanePlacement>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let flushOnHideBound = false;

/** Quiet time before a burst of placement changes becomes one settings write. */
export const PLACEMENT_FLUSH_MS = 500;

/** Where this view was last left, or undefined if it never was. */
export function lastPlacement(viewId: string): PanePlacement | undefined {
  return pending.get(viewId) ?? all()[viewId];
}

/**
 * Record where a view is now. A no-op when nothing moved (this runs from a store
 * subscriber on every layout change), and debounced when something did: a
 * settings write notifies every settings subscriber in the app and sends a `PUT`.
 */
export function rememberPlacement(viewId: string, placement: PanePlacement): void {
  if (lastPlacement(viewId) === placement) return;
  pending.set(viewId, placement);
  if (flushTimer !== null) clearTimeout(flushTimer);
  flushTimer = setTimeout(flushPlacements, PLACEMENT_FLUSH_MS);
  if (!flushOnHideBound && typeof window !== 'undefined') {
    // Closing the app inside the debounce window would otherwise drop the last
    // move. Best effort: the PUT may not outlive the page either.
    flushOnHideBound = true;
    window.addEventListener('pagehide', flushPlacements);
  }
}

/** Write every pending placement now, as one settings change. */
export function flushPlacements(): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (pending.size === 0) return;
  const next = { ...all(), ...Object.fromEntries(pending) };
  pending.clear();
  // Optimistic: `setSetting` updates the local override synchronously, so the
  // next open reads the new placement even if the network write is still in
  // flight or fails outright.
  setSetting(PANE_PLACEMENT_KEY, JSON.stringify(next)).catch(() => {});
}
