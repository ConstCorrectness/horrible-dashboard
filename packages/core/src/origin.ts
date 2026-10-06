/**
 * Backend origin. The browser layout leaves it null — paths stay relative and
 * the Vite dev server proxies /api and /ws to the backend. The Tauri entry sets
 * an absolute origin learned from the shell's `backend_status` command (the
 * shell spawns and supervises the backend), so dev and packaged desktop builds
 * share one code path with no proxy involved.
 */
let backendOrigin: string | null = null;

/** Called once by the app entry before any API/WS use. null = relative paths. */
export function initBackendOrigin(origin: string | null): void {
  backendOrigin = origin ? origin.replace(/\/+$/, '') : null;
}

export function getBackendOrigin(): string | null {
  return backendOrigin;
}

/** Absolute-or-relative HTTP URL for a path like `/api/...`. */
export function apiUrl(path: string): string {
  return `${backendOrigin ?? ''}${path}`;
}

/**
 * Where WebSockets go when that differs from HTTP. Only a split deployment sets it:
 * a frontend on Vercel reaches `/api` through a same-origin rewrite (so the session
 * cookie stays first-party) but a rewrite cannot carry a WebSocket, so `/ws` dials
 * the hub directly. Built in from `VITE_WS_ORIGIN`; null everywhere else.
 */
let wsOrigin: string | null = null;

/** Called once by the app entry. null = WebSockets follow {@link initBackendOrigin}. */
export function initWsOrigin(origin: string | null): void {
  wsOrigin = origin ? origin.replace(/\/+$/, '') : null;
}

export function getWsOrigin(): string | null {
  return wsOrigin ?? backendOrigin;
}

/** WS URL for a path like `/ws` (http→ws scheme mapping when origin is set). */
export function wsUrl(path: string): string {
  const origin = wsOrigin ?? backendOrigin;
  if (origin) return `${origin.replace(/^http/, 'ws')}${path}`;
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}${path}`;
}
