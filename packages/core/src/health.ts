/**
 * Backend reachability, polled once for the whole app.
 *
 * This used to be a whole center pane (`dashboard.backendStatus`) rendering one
 * line of text — the clearest case of "a readout became a destination because a
 * pane was the only way to show anything". The signal is worth keeping and the
 * pane was not, so it lives here and renders in the minibuffer's status line.
 *
 * A store rather than a hook so there is exactly one poll regardless of how many
 * things display it, and so the interval stops when nothing is listening.
 */
import { apiGet } from './api';

export interface BackendHealth {
  /** null while the first probe is still in flight — not the same as "down". */
  reachable: boolean | null;
  app?: string;
  version?: string;
  status?: string;
  error?: string;
}

const POLL_MS = 10_000;

let state: BackendHealth = { reachable: null };
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function set(next: BackendHealth): void {
  // Reference equality is the re-render gate for useSyncExternalStore, so only
  // publish a new object when something actually changed.
  if (
    next.reachable === state.reachable &&
    next.version === state.version &&
    next.error === state.error
  ) {
    return;
  }
  state = next;
  listeners.forEach((l) => l());
}

function poll(): void {
  apiGet<{ status: string; app: string; version: string }>('/health')
    .then((h) => set({ reachable: true, status: h.status, app: h.app, version: h.version }))
    .catch((e: unknown) => set({ reachable: false, error: String(e) }));
}

export const backendHealth = {
  getSnapshot(): BackendHealth {
    return state;
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    if (timer === null) {
      poll();
      timer = setInterval(poll, POLL_MS);
    }
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0 && timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
  },
};
