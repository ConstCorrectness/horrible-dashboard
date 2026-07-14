/**
 * Client for the real backend browser engine (the `browser` `/ws` channel).
 *
 * "Full mode" runs a headless Chromium on the local backend and server-renders it:
 * the backend pushes JPEG `frame` events, and we relay interactions
 * (navigate/click/scroll/type/key) back over the same channel — the vizdoom/visualizer
 * pattern. Agent-facing ops (content/snapshot/scrape/screenshot) are request/reply,
 * correlated by a monotonic `id`, so the same live session serves both the human panel
 * and the agent tools (one WS connection ⇒ one shared browser). The engine is gated by
 * `HORRIBLE_ENABLE_SERVER_BROWSER=1` on the backend; when off, ops reject with a notice.
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
  snapshot: (): Promise<{ url: string; title: string; elements: SnapshotElement[] }> =>
    requestOp('snapshot'),
  scrape: (selector: string) => requestOp('scrape', { selector }),
  screenshot: (): Promise<{ frame: string }> => requestOp('screenshot'),
  clickRef: (ref: number) => requestOp<null>('click_ref', { ref }),
  typeRef: (ref: number, text: string) => requestOp<null>('type_ref', { ref, text }),
  info: (): Promise<{ url: string; title: string }> => requestOp('info'),
};

/** Stop the backend session for this connection (releases Chromium). */
export function stopSession(): void {
  sendChannel('browser', 'stop', {});
}
