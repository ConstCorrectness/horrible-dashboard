import { subscribeChannel, type WsMessage } from './ws';

/**
 * Unified I/O event stream for the observability surfaces. Merges two sources:
 * `client` events recorded in the API client (frontend → backend round-trips)
 * and `inbound`/`outbound` events streamed from the backend over `/ws`. See
 * docs/modules/observability.md.
 */
export type IoSource = 'client' | 'inbound' | 'outbound';

export interface IoEvent {
  id: number | string;
  ts: number; // seconds since epoch
  source: IoSource;
  method: string;
  target: string;
  status?: number | null;
  duration_ms?: number | null;
  request_bytes?: number | null;
  response_bytes?: number | null;
  error?: string | null;
}

const MAX_EVENTS = 300;
let events: IoEvent[] = [];
const listeners = new Set<() => void>();
let clientSeq = 0;
let boundToWs = false;

function emit(): void {
  for (const listener of listeners) listener();
}

function push(event: IoEvent): void {
  // New array reference each push so useSyncExternalStore sees a change.
  events = [...events, event].slice(-MAX_EVENTS);
  emit();
}

/** Record a frontend → backend round-trip (called by the API client). */
export function recordClientIo(event: Omit<IoEvent, 'id' | 'source' | 'ts'>): void {
  push({ ...event, id: `c${clientSeq++}`, source: 'client', ts: Date.now() / 1000 });
}

function bindWs(): void {
  if (boundToWs) return;
  boundToWs = true;
  subscribeChannel('telemetry', (msg: WsMessage) => {
    if (msg.event === 'io' && msg.data) push(msg.data as IoEvent);
  });
}

/** A store shaped for React's useSyncExternalStore. Binds the WS on first use. */
export const telemetryStore = {
  subscribe(listener: () => void): () => void {
    bindWs();
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): IoEvent[] {
    return events;
  },
  clear(): void {
    events = [];
    emit();
  },
};
