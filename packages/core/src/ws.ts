/**
 * The shell's single shared WebSocket to the backend `/ws`. Modules subscribe to
 * named channels rather than opening their own sockets. Reconnects with backoff.
 */
import type { WsMessage } from '@horribledashboard/sdk';

import { wsUrl } from './origin';

export type { WsMessage };

type Handler = (msg: WsMessage) => void;

const handlers = new Map<string, Set<Handler>>();
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
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10_000);
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
