/**
 * The shell's single shared WebSocket to the backend `/ws`. Modules subscribe to
 * named channels rather than opening their own sockets. Reconnects with backoff.
 */
import type { WsMessage } from '@horribledashboard/sdk';

import { wsUrl } from './origin';

export type { WsMessage };

type Handler = (msg: WsMessage) => void;

const handlers = new Map<string, Set<Handler>>();
const openListeners = new Set<() => void>();
const closeListeners = new Set<() => void>();
let socket: WebSocket | null = null;
let backoff = 500;

function connect(): void {
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  socket = new WebSocket(wsUrl('/ws'));

  socket.onopen = () => {
    backoff = 500;
    openListeners.forEach((l) => l());
  };
  socket.onmessage = (e: MessageEvent<string>) => {
    let msg: WsMessage;
    try {
      msg = JSON.parse(e.data) as WsMessage;
    } catch {
      return;
    }
    handlers.get(msg.channel)?.forEach((h) => h(msg));
  };
  socket.onclose = () => {
    socket = null;
    closeListeners.forEach((l) => l());
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10_000);
  };
}

/**
 * Run `listener` every time the socket (re)connects — and once now if already
 * open. Used to (re)push state the backend forgets across reconnects, e.g. the
 * agent capability manifest. Returns an unsubscribe function. Connects lazily.
 */
export function onSocketOpen(listener: () => void): () => void {
  connect();
  openListeners.add(listener);
  if (socket && socket.readyState === WebSocket.OPEN) listener();
  return () => {
    openListeners.delete(listener);
  };
}

/**
 * Run `listener` every time the socket drops. The counterpart of `onSocketOpen`,
 * and the thing whose absence made every live pane lie: a module could learn that
 * the backend had come back but never that it had gone, so anything driven by
 * pushed events — a notebook mid-run, a download's progress — simply held its
 * last frame. The notebook kept reading "● busy" with queued cells beneath it
 * while no kernel existed at all, which is indistinguishable from a slow cell and
 * is the state a `--reload` backend restart leaves behind every time.
 *
 * Fires on the drop only. Reconnection is `onSocketOpen`, which already runs on
 * every (re)connect, so the two together are the whole lifecycle.
 */
export function onSocketClose(listener: () => void): () => void {
  connect();
  closeListeners.add(listener);
  return () => {
    closeListeners.delete(listener);
  };
}

/** Subscribe to a channel; returns an unsubscribe function. Connects lazily. */
export function subscribeChannel(channel: string, handler: Handler): () => void {
  connect();
  let set = handlers.get(channel);
  if (!set) {
    set = new Set();
    handlers.set(channel, set);
  }
  set.add(handler);
  return () => {
    set?.delete(handler);
  };
}

/**
 * Send a message to the backend on a channel. Connects lazily; if the socket
 * isn't open yet, waits for `open` once and flushes. Used by the agent channel
 * to drive the backend orchestrator (`ask`, `tool_result`).
 */
export function sendChannel(channel: string, event: string, data?: unknown): void {
  connect();
  const payload = JSON.stringify({ channel, event, data });
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(payload);
  } else {
    socket?.addEventListener('open', () => socket?.send(payload), { once: true });
  }
}
