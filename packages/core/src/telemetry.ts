import { subscribeChannel, type WsMessage } from './ws';

/**
 * Unified I/O event stream for the observability surfaces. Merges two sources:
 * `client` events recorded in the API client (frontend → backend round-trips)
 * and `inbound`/`outbound` events streamed from the backend over `/ws`. See
 * docs/modules/observability.md.
 */
export type IoSource = 'client' | 'inbound' | 'outbound' | 'ws' | 'browser';

/** What the egress policy decided about a `browser` request. */
export type IoVerdict = 'allowed' | 'blocked' | 'pending';

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
  // Expandable detail — redacted/truncated at capture, absent when not safely
  // capturable (see backend/modules/telemetry/instrument.py and api.ts).
  request_headers?: Record<string, string> | null;
  response_headers?: Record<string, string> | null;
  request_body?: string | null;
  response_body?: string | null;
  // `browser`-only: Chromium's own resource classification (document, script,
  // image, xhr, …) and whether the SSRF guard let the request out.
  resource_type?: string | null;
  verdict?: IoVerdict | null;
}

const MAX_EVENTS = 300;
let events: IoEvent[] = [];
const listeners = new Set<() => void>();
let clientSeq = 0;
let boundToWs = false;

function emit(): void {
  for (const listener of listeners) listener();
}

function eventKey(e: IoEvent): string {
  return `${e.source}-${e.id}`;
}

function push(event: IoEvent): void {
  // New array reference each push so useSyncExternalStore sees a change. Events
  // are upserted by (source, id): the backend re-emits an event under the same id
  // to fill in a streaming response body once the stream finishes (see
  // recorder.amend), and replaying the backlog on reconnect mustn't duplicate.
  const key = eventKey(event);
  const idx = events.findIndex((e) => eventKey(e) === key);
  if (idx === -1) {
    events = [...events, event].slice(-MAX_EVENTS);
  } else {
    events = events.slice();
    events[idx] = event;
  }
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
