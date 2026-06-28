/**
 * Client for the `/ws` `peerchat` channel: direct 1:1 messaging with a connected
 * peer. The backend relays each message over the signed peer wire and mirrors it to
 * this node's tabs; history is kept server-side per peer. See docs/modules/network.mdx.
 */
import { sendChannel, subscribeChannel } from '../../ws';

export interface ChatMessage {
  id: string;
  nodeId: string;
  from: string;
  text: string;
  ts: number;
  direction: 'in' | 'out';
}

export interface ChatEvent {
  kind: 'history' | 'message' | 'error';
  nodeId: string;
  messages?: ChatMessage[];
  message?: ChatMessage;
  error?: string;
}

/** Subscribe to peer-chat events. Returns an unsubscribe function. */
export function subscribeChat(handler: (event: ChatEvent) => void): () => void {
  return subscribeChannel('peerchat', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'history') {
      handler({
        kind: 'history',
        nodeId: String(d.nodeId ?? ''),
        messages: (d.messages as ChatMessage[]) ?? [],
      });
    } else if (msg.event === 'message') {
      handler({
        kind: 'message',
        nodeId: String(d.nodeId ?? ''),
        message: d as unknown as ChatMessage,
      });
    } else if (msg.event === 'error') {
      handler({ kind: 'error', nodeId: String(d.nodeId ?? ''), error: String(d.message ?? '') });
    }
  });
}

/** Open a conversation with a peer and request its backlog. */
export function chatOpen(nodeId: string): void {
  sendChannel('peerchat', 'open', { nodeId });
}

export function chatSend(nodeId: string, text: string): void {
  sendChannel('peerchat', 'send', { nodeId, text });
}

export function chatClose(nodeId: string): void {
  sendChannel('peerchat', 'close', { nodeId });
}
