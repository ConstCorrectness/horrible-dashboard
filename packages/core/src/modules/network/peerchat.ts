/**
 * Client for the `/ws` `peerchat` channel: direct 1:1 messaging, **by person**.
 *
 * The channel's vocabulary is `personId`, not `nodeId`. Which machine of theirs a
 * message actually travels over is chosen by the backend at send time and reported
 * back on the message for diagnostics — it is a route, not an address you pick.
 * History is persisted server-side in `social_messages`, so it survives a restart
 * and a friend switching computers.
 *
 * See docs/modules/social.mdx (Messages).
 */
import { sendChannel, subscribeChannel } from '../../ws';

export interface ChatMessage {
  id: string;
  personId: string | null;
  /** The machine it travelled over. Diagnostics only — never group by this. */
  nodeId: string | null;
  from: string;
  text: string;
  ts: number;
  direction: 'in' | 'out';
  read: boolean;
}

export interface ChatEvent {
  kind: 'history' | 'message' | 'error' | 'unread';
  personId: string;
  messages?: ChatMessage[];
  message?: ChatMessage;
  /** `personId -> unread`; only conversations with something unread appear. */
  counts?: Record<string, number>;
  error?: string;
}

/** Subscribe to peer-chat events. Returns an unsubscribe function. */
export function subscribeChat(handler: (event: ChatEvent) => void): () => void {
  return subscribeChannel('peerchat', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    const personId = String(d.personId ?? '');
    if (msg.event === 'history') {
      handler({ kind: 'history', personId, messages: (d.messages as ChatMessage[]) ?? [] });
    } else if (msg.event === 'message') {
      handler({ kind: 'message', personId, message: d as unknown as ChatMessage });
    } else if (msg.event === 'unread') {
      handler({
        kind: 'unread',
        personId,
        counts: (d.counts as Record<string, number>) ?? {},
      });
    } else if (msg.event === 'error') {
      handler({ kind: 'error', personId, error: String(d.message ?? '') });
    }
  });
}

/** Open a conversation with someone and request its backlog (marks it read). */
export function chatOpen(personId: string): void {
  sendChannel('peerchat', 'open', { personId });
}

export function chatSend(personId: string, text: string): void {
  sendChannel('peerchat', 'send', { personId, text });
}

export function chatClose(personId: string): void {
  sendChannel('peerchat', 'close', { personId });
}

/** Mark a conversation read without opening it. */
export function chatMarkRead(personId: string): void {
  sendChannel('peerchat', 'read', { personId });
}

/**
 * Subscribe to unread badges without opening any conversation — what a friends
 * list wants. Kept separate from `chatOpen('')` so the intent reads at the call
 * site rather than as an empty-string special case.
 */
export function chatRequestUnread(): void {
  sendChannel('peerchat', 'unread', {});
}
