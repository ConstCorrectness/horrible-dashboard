/**
 * Only `http`/`https` may be loaded into a browser surface or handed to the OS, so a
 * compromised or buggy frontend can never steer one at `file:`/`app:` local
 * resources — where it would run with app privileges rather than as a foreign page —
 * or at a `javascript:` URL. Throws with a message naming the refused URL.
 */
export function parseWebUrl(url: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error(`not a URL: ${url}`);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`refusing to load non-http(s) URL: ${url}`);
  }
  return parsed;
}

export function isWebUrl(url: string): boolean {
  try {
    parseWebUrl(url);
    return true;
  } catch {
    return false;
  }
}

/** Pane rectangle in logical (CSS/DIP) pixels, as the frontend measures it. */
export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Clamp to something a view can actually occupy, in the integer DIPs Electron takes.
 * A pane can legitimately measure 0×0 (collapsed, or on a workspace that isn't
 * showing), and a non-finite value would make `setBounds` throw.
 */
export function sanitizeBounds(b: Bounds): Bounds {
  const n = (v: number) => (Number.isFinite(v) ? Math.round(v) : 0);
  return { x: n(b.x), y: n(b.y), width: Math.max(1, n(b.width)), height: Math.max(1, n(b.height)) };
}

/**
 * A `window.open` that asked for a size or position is a *window* its opener still
 * talks to: OAuth and payment flows post their result back through `window.opener`.
 * Everything else (`target=_blank`, a bare `window.open(url)`) becomes a tab.
 */
export function wantsPopupWindow(features: string): boolean {
  return features
    .split(',')
    .map((f) => f.split('=')[0]?.trim().toLowerCase())
    .some((k) => k === 'width' || k === 'height' || k === 'left' || k === 'top' || k === 'popup');
}
