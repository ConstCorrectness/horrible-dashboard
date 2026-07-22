/**
 * Client for the real backend browser engine (the `browser` `/ws` channel).
 *
 * "Full mode" runs a headless Chromium on the local backend and server-renders it:
 * the backend pushes JPEG `frame` events, and we relay interactions
 * (navigate/click/scroll/type/key) back over the same channel — the vizdoom/visualizer
 * pattern. Agent-facing ops (content/snapshot/scrape/screenshot) are request/reply,
 * correlated by a monotonic `id`, so the same live session serves both the human panel
 * and the agent tools. The engine is gated by `HORRIBLE_ENABLE_SERVER_BROWSER=1` on the
 * backend; when off, ops reject with a notice.
 *
 * **One WS connection ⇒ one shared browser, and one shared *page*.** Every open
 * `browser.view` pane drives it, so two panes do not show two tabs — navigating one
 * moves the page under the other, which keeps rendering its stale frame. Panes claim
 * the engine with `acquireSession()` so one closing doesn't stop it for the rest;
 * genuinely independent tabs need one `page` per pane inside a single per-profile
 * context on the backend (see the scope note in docs/modules/browser.mdx).
 *
 * See docs/modules/browser.mdx.
 */
import type { WsMessage } from '@horribledashboard/sdk';

import { sendChannel, subscribeChannel } from '../../ws';

export interface BrowserFrame {
  frame: string; // data:image/jpeg;base64 URI
  url: string;
  title: string;
}

/** One interactable element from an agent `snapshot`. */
export interface SnapshotElement {
  ref: number;
  role: string;
  name: string;
  value: string;
  x: number;
  y: number;
}

export interface PageContent {
  url: string;
  title: string;
  author: string | null;
  text: string;
}

/** Result of the `capture` op: the stored self-contained page + its extraction. */
export interface PageCapture {
  artifact_id: string;
  url: string;
  title: string;
  author: string | null;
  text: string;
}

/**
 * One image/video found on the live page, with the text that describes it.
 * `context` is the surrounding prose (figcaption, nearest heading) — the app has no
 * multimodal embedder, so these words are what make the asset searchable once saved.
 */
export interface MediaItem {
  src: string;
  kind: 'image' | 'video' | 'embed';
  alt: string;
  title: string;
  width: number | null;
  height: number | null;
  duration?: number | null;
  poster?: string | null;
  context: string[];
}

export interface PageMedia {
  url: string;
  title: string;
  images: MediaItem[];
  videos: MediaItem[];
}

/** One request the embedded Chromium currently has in flight. */
export interface BrowserConnection {
  id: number;
  url: string;
  method: string;
  resourceType: string | null;
  startedAt: number;
  elapsedMs: number;
}

type PendingResolver = {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
};

let nextId = 1;
const pending = new Map<number, PendingResolver>();
let subscribed = false;

function ensureSubscribed(): void {
  if (subscribed) return;
  subscribed = true;
  subscribeChannel('browser', (msg: WsMessage) => {
    const data = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'result') {
      const id = data.id as number | undefined;
      if (id != null && pending.has(id)) {
        pending.get(id)!.resolve(data.result);
        pending.delete(id);
      }
    } else if (msg.event === 'error') {
      const id = data.id as number | undefined;
      if (id != null && pending.has(id)) {
        pending.get(id)!.reject(new Error(String(data.message ?? 'browser error')));
        pending.delete(id);
      }
    } else if (msg.event === 'disabled') {
      // Fail every in-flight op so callers fall back to iframe/reader mode.
      const err = new Error(String(data.message ?? 'browser engine disabled'));
      pending.forEach((p) => p.reject(err));
      pending.clear();
    }
  });
}

/**
 * Subscribe to live `frame` (and `title`/`url`) updates from the backend session.
 * Returns an unsubscribe function. Frames arrive after every interaction plus on an
 * idle cadence, so a listener always sees the current viewport.
 */
export function subscribeFrames(onFrame: (f: BrowserFrame) => void): () => void {
  return subscribeChannel('browser', (msg: WsMessage) => {
    if (msg.event === 'frame') onFrame(msg.data as BrowserFrame);
  });
}

/**
 * Subscribe to the live set of in-flight Chromium requests. Emitted whenever a
 * request starts or settles, so a listener always holds the current set — this is
 * the "open connections" view; completed requests land in the I/O stream instead.
 */
export function subscribeConnections(
  onConnections: (conns: BrowserConnection[]) => void,
): () => void {
  return subscribeChannel('browser', (msg: WsMessage) => {
    if (msg.event === 'connections') {
      onConnections((msg.data as { connections: BrowserConnection[] }).connections);
    }
  });
}

/**
 * Subscribe to live global `error` events from the backend session (e.g. startup errors).
 */
export function subscribeErrors(onError: (msg: string) => void): () => void {
  return subscribeChannel('browser', (msg: WsMessage) => {
    const data = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'error' && !data.id) {
      onError(String(data.message ?? 'browser error'));
    }
  });
}

/** Start (or attach to) the session for this connection, optionally at `url`. */
export function startSession(url?: string): void {
  ensureSubscribed();
  sendChannel('browser', 'start', url ? { url } : {});
}

/** Fire-and-forget interaction (human input); the effect is the next frame. */
export function sendInput(op: string, args: Record<string, unknown> = {}): void {
  ensureSubscribed();
  sendChannel('browser', op, args);
}

/** Request/reply op (agent tools + navigation confirmations); resolves with the result. */
export function requestOp<T = unknown>(
  op: string,
  args: Record<string, unknown> = {},
  timeoutMs = 30_000,
): Promise<T> {
  ensureSubscribed();
  const id = nextId++;
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`browser ${op} timed out`));
      }
    }, timeoutMs);
    pending.set(id, {
      resolve: (v) => {
        clearTimeout(timer);
        resolve(v as T);
      },
      reject: (e) => {
        clearTimeout(timer);
        reject(e);
      },
    });
    sendChannel('browser', op, { ...args, id });
  });
}

// --- Agent-facing ops (also used by the panel's dev affordances) ------------

export const engine = {
  navigate: (url: string) => requestOp<null>('navigate', { url }),
  content: (): Promise<PageContent> => requestOp<PageContent>('content'),
  /** Capture the live page as a self-contained HTML artifact (server-stored).
   * Generous timeout: the backend fetches and inlines every subresource. */
  capture: (): Promise<PageCapture> => requestOp<PageCapture>('capture', {}, 120_000),
  snapshot: (): Promise<{ url: string; title: string; elements: SnapshotElement[] }> =>
    requestOp('snapshot'),
  scrape: (selector: string) => requestOp('scrape', { selector }),
  media: (): Promise<PageMedia> => requestOp<PageMedia>('media'),
  screenshot: (): Promise<{ frame: string }> => requestOp('screenshot'),
  clickRef: (ref: number) => requestOp<null>('click_ref', { ref }),
  typeRef: (ref: number, text: string) => requestOp<null>('type_ref', { ref, text }),
  info: (): Promise<{ url: string; title: string }> => requestOp('info'),
};

// How many browser panes are currently mounted and relying on the shared session.
// The engine is shared per WS connection, so ONE pane unmounting must not stop it
// out from under the others — that left every remaining pane frozen forever on a
// stale frame with no error, because nothing tells a pane its session died
// (`stop_for` pops the session and sets `_closing`, killing the frame pump, while
// the next request/reply op silently builds a replacement nobody is driving).
let sessionRefs = 0;

/**
 * Claim the shared session for a mounting pane. Returns the matching release,
 * which stops the engine only when the last pane lets go.
 */
export function acquireSession(): () => void {
  sessionRefs += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    sessionRefs -= 1;
    // Deferred: React StrictMode unmounts and immediately remounts in dev, which
    // would otherwise tear the engine down and rebuild it on every mount.
    queueMicrotask(() => {
      if (sessionRefs <= 0) {
        sessionRefs = 0;
        stopSession();
      }
    });
  };
}

/**
 * Stop the backend session for this connection (releases Chromium). Prefer
 * `acquireSession`'s release — calling this directly stops the engine for every
 * pane sharing the connection.
 */
export function stopSession(): void {
  sendChannel('browser', 'stop', {});
}
