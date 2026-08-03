/**
 * Shared connector state — "what can my agent reach", readable from anywhere.
 *
 * `listConnectors()` had exactly one caller in the whole app (the home tile row),
 * which owned the result in a `useState`. So a pane that needed to know whether
 * Google was connected had two options: fetch and poll its own copy, or do what
 * they all actually did — call the backend, get a 409, and render a dead end. The
 * information existed; there was just no way to ask for it.
 *
 * Two things live here:
 *
 * - **the cache** — one fetch, many subscribers, refreshed after any connect or
 *   disconnect, so a tile row and a pane can never disagree about the same account.
 * - **`requestConnect`** — a pane saying "the user needs GitHub" without knowing
 *   anything about how connecting works. The home tile row listens and opens the
 *   real popover, so there is still exactly one implementation of the connect flow
 *   and panes never grow their own.
 */
import { listConnectors, type Connector } from './api';

export interface ConnectorsState {
  connectors: Connector[];
  /** `unavailable` is the backend being unreachable, not "nothing is connected" —
   * the tile row renders nothing at all in that case rather than a row of
   * plausible-looking disconnected tiles. */
  phase: 'loading' | 'ready' | 'unavailable';
}

const EMPTY: ConnectorsState = { connectors: [], phase: 'loading' };

let state: ConnectorsState = EMPTY;
const listeners = new Set<() => void>();
let inFlight: Promise<ConnectorsState> | null = null;
let everLoaded = false;

function emit(next: ConnectorsState): void {
  state = next;
  for (const listener of listeners) listener();
}

/** Re-read the connector list and publish it. Call after any connect/disconnect. */
export function refreshConnectors(): Promise<ConnectorsState> {
  if (inFlight) return inFlight;
  inFlight = listConnectors()
    .then((connectors) => {
      const next: ConnectorsState = { connectors, phase: 'ready' };
      emit(next);
      return next;
    })
    .catch(() => {
      const next: ConnectorsState = { ...state, phase: 'unavailable' };
      emit(next);
      return next;
    })
    .finally(() => {
      inFlight = null;
      everLoaded = true;
    });
  return inFlight;
}

export const connectorsStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    if (!everLoaded && !inFlight) void refreshConnectors();
    return () => {
      listeners.delete(listener);
    };
  },
  getState(): ConnectorsState {
    return state;
  },
  reset(): void {
    everLoaded = false;
    inFlight = null;
    emit(EMPTY);
  },
};

/** Look up one connector in the current cache. `undefined` means "not loaded yet
 * or no such connector" — deliberately not `false`, which would read as a
 * confident "not connected" before the first fetch has landed. */
export function connectorById(id: string): Connector | undefined {
  return state.connectors.find((c) => c.id === id);
}

// ---- the connect request bus -------------------------------------------------

type ConnectRequestListener = (connectorId: string) => void;

const connectRequestListeners = new Set<ConnectRequestListener>();

/**
 * Ask the app to walk the user through connecting `connectorId`.
 *
 * Fire-and-forget by design: the caller is a pane that knows what it needs, not
 * where the UI for it lives. Whoever owns the connect UI (the home tile row)
 * subscribes and opens it.
 */
export function requestConnect(connectorId: string): void {
  for (const listener of connectRequestListeners) listener(connectorId);
}

/** Subscribe to connect requests. Returns an unsubscribe. */
export function onConnectRequested(listener: ConnectRequestListener): () => void {
  connectRequestListeners.add(listener);
  return () => connectRequestListeners.delete(listener);
}
