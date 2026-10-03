/**
 * Page zoom for the whole dashboard — Ctrl+= / Ctrl+- / Ctrl+0 and Ctrl+wheel, the
 * way a browser zooms a page.
 *
 * In the **browser** layout this module does nothing: the browser's own page zoom
 * already owns those keys, and binding them here would `preventDefault` a working
 * feature away. On the **desktop** the WebView has no zoom keys of its own (Tauri
 * turns them off), so the shell's `window_set_zoom` does the zooming and this module
 * supplies the keys, the steps and the memory.
 *
 * Native zoom rather than CSS `zoom` on the root: every pane — canvases, CodeMirror,
 * pointer math, the 3D views — sees an ordinary page at a different device-pixel
 * ratio, exactly as it would under the browser's zoom, so none of them needs to know.
 *
 * The level is remembered per machine (localStorage, guarded) — a zoom is a
 * property of the screen you are sitting at, not of your account.
 */
import { windowControl } from './window';

/** Chromium's own zoom steps. */
export const ZOOM_LEVELS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];

const STORAGE_KEY = 'horrible.zoom';
/** Wheel distance (px) that makes one zoom step: a mouse notch is ~100, a
 * trackpad pinch emits many small deltas that add up to it. */
const WHEEL_STEP_PX = 60;

let level = 1;
const listeners = new Set<(level: number) => void>();

/** Is native page zoom available here (the desktop shell)? */
export function canZoom(): boolean {
  return typeof windowControl()?.setZoom === 'function';
}

export function currentZoom(): number {
  return level;
}

export function subscribeZoom(listener: (level: number) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function remembered(): number {
  try {
    const value = Number(globalThis.localStorage?.getItem(STORAGE_KEY));
    return Number.isFinite(value) && value > 0 ? value : 1;
  } catch {
    return 1;
  }
}

/** The level `steps` rungs away from `from` on the ladder. A level between rungs
 * (set some other way) moves to the next rung in that direction. */
export function stepZoom(from: number, steps: number): number {
  let current = from;
  for (let i = 0; i < Math.abs(steps); i++) {
    current =
      steps > 0
        ? (ZOOM_LEVELS.find((l) => l > current + 1e-6) ?? ZOOM_LEVELS[ZOOM_LEVELS.length - 1])
        : ([...ZOOM_LEVELS].reverse().find((l) => l < current - 1e-6) ?? ZOOM_LEVELS[0]);
  }
  return current;
}

/** Apply `next`; answers the level the shell actually set (null if unavailable). */
export async function setZoom(next: number): Promise<number | null> {
  const control = windowControl();
  if (!control?.setZoom) return null;
  const applied = await control.setZoom(next);
  level = applied;
  try {
    if (applied === 1) globalThis.localStorage?.removeItem(STORAGE_KEY);
    else globalThis.localStorage?.setItem(STORAGE_KEY, String(applied));
  } catch {
    // Remembering is a convenience; the zoom itself is applied.
  }
  for (const listener of listeners) listener(applied);
  return applied;
}

export const zoomIn = () => setZoom(stepZoom(level, 1));
export const zoomOut = () => setZoom(stepZoom(level, -1));
export const resetZoom = () => setZoom(1);

/**
 * Restore the remembered level and listen for Ctrl+wheel. Desktop only; call once
 * at boot after the window control is installed. Returns an uninstaller.
 *
 * The wheel listener runs in the bubble phase on `window` and yields to anything
 * that already handled the event: a canvas that zooms itself on Ctrl+wheel (the
 * model graph, a map) calls `preventDefault`, and keeps its gesture.
 */
export function installZoom(): () => void {
  if (!canZoom()) return () => {};
  const saved = remembered();
  if (saved !== 1) void setZoom(saved);

  let travelled = 0;
  const onWheel = (e: WheelEvent) => {
    if (!e.ctrlKey || e.defaultPrevented) return;
    e.preventDefault();
    travelled += e.deltaY;
    if (Math.abs(travelled) < WHEEL_STEP_PX) return;
    const steps = travelled < 0 ? 1 : -1;
    travelled = 0;
    void setZoom(stepZoom(level, steps));
  };
  window.addEventListener('wheel', onWheel, { passive: false });
  return () => window.removeEventListener('wheel', onWheel);
}
