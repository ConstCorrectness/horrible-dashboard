/**
 * Where each view's window was last left, so it opens back there.
 *
 * A desktop where every window opens on the same diagonal cascade is a desktop
 * you re-arrange every time you use it. Snapping the agent to the right edge is
 * a decision about where that pane *belongs*, and it should outlive closing it —
 * which the frame alone cannot do, because a closed pane has no `WindowState` to
 * remember anything with.
 *
 * Keyed by **view id, not pane instance**: instance ids are minted per open, so
 * keying by one would remember a placement that can never be looked up again.
 *
 * Global rather than per-workspace, and a setting rather than the layout blob —
 * the same call `rail-prefs` makes for the same reason: where you keep a tool is
 * a habit, not a layout. It rides `frame.windowPlacement` as one JSON string, so
 * it hydrates at boot and persists server-side for free.
 */
import { getSetting, setSetting } from '../settings';
import type { SnapZone, WindowRect } from './types';

export const WINDOW_PLACEMENT_KEY = 'frame.windowPlacement';

export interface WindowPlacement {
  rect: WindowRect;
  /**
   * The zone it was snapped to, if any.
   *
   * Kept alongside the rect rather than derived from it: a snapped window and a
   * window merely dragged to the same pixels behave differently the moment the
   * surface resizes or the user un-snaps it, and restoring the geometry without
   * the state would silently downgrade the first into the second.
   */
  snap?: SnapZone;
}

const SNAP_ZONES: readonly SnapZone[] = [
  'left',
  'right',
  'top',
  'bottom',
  'tl',
  'tr',
  'bl',
  'br',
  'max',
];

function isRect(value: unknown): value is WindowRect {
  if (!value || typeof value !== 'object') return false;
  const r = value as Record<string, unknown>;
  return (['x', 'y', 'w', 'h'] as const).every(
    (k) => typeof r[k] === 'number' && Number.isFinite(r[k]),
  );
}

/** Tolerant parse — a malformed blob degrades to "remember nothing", never throws. */
function parse(raw: string | undefined): Record<string, WindowPlacement> {
  if (!raw) return {};
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null;
    if (!data || typeof data !== 'object') return {};
    const out: Record<string, WindowPlacement> = {};
    for (const [viewId, value] of Object.entries(data)) {
      const entry = value as { rect?: unknown; snap?: unknown } | null;
      if (!entry || !isRect(entry.rect)) continue;
      out[viewId] = {
        rect: entry.rect,
        ...(SNAP_ZONES.includes(entry.snap as SnapZone) ? { snap: entry.snap as SnapZone } : {}),
      };
    }
    return out;
  } catch {
    return {};
  }
}

// Parsed once per distinct raw string: `lastPlacement` is called on every window
// open, and re-parsing the whole bag each time to read one entry is waste.
let cache: { raw: string | undefined; value: Record<string, WindowPlacement> } | null = null;

function all(): Record<string, WindowPlacement> {
  const raw = getSetting<string>(WINDOW_PLACEMENT_KEY);
  if (!cache || cache.raw !== raw) cache = { raw, value: parse(raw) };
  return cache.value;
}

/** Where this view's window was last left, or undefined if it never had one. */
export function lastPlacement(viewId: string): WindowPlacement | undefined {
  return all()[viewId];
}

/**
 * Record where a view's window is now.
 *
 * A no-op when nothing moved, because this is called from a store subscriber on
 * every layout change — writing an identical value would emit a settings change
 * and a network PUT for every focus, tab switch and keystroke-driven relayout.
 */
export function rememberPlacement(viewId: string, rect: WindowRect, snap?: SnapZone): void {
  const current = all()[viewId];
  if (current && sameRect(current.rect, rect) && current.snap === snap) return;
  const next = { ...all(), [viewId]: { rect, ...(snap ? { snap } : {}) } };
  // Optimistic: `setSetting` updates the local override synchronously, so the
  // next open reads the new placement even if the network write is still in
  // flight or fails outright. A dropped write costs durability, not correctness.
  setSetting(WINDOW_PLACEMENT_KEY, JSON.stringify(next)).catch(() => {});
}

function sameRect(a: WindowRect, b: WindowRect): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}
