/**
 * Client for the `/ws` `collab` channel: shared-pane state sync. Thin primitives a
 * pane uses to join a room, push edits (with its last-known revision), and receive
 * authoritative state. Conflict handling is rev-checked last-writer-wins on the
 * backend (see modules/network collab.py) — this is the groundwork a real CRDT
 * would replace.
 */
import { sendChannel, subscribeChannel } from '../../ws';

export interface CollabUpdate {
  kind: 'state' | 'op' | 'rejected';
  rev: number;
  text: string;
  from?: string;
}

/** Subscribe to updates for one shared pane. Returns an unsubscribe function. */
export function subscribeCollab(
  paneKey: string,
  handler: (update: CollabUpdate) => void,
): () => void {
  return subscribeChannel('collab', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    if (d.paneKey !== paneKey) return;
    if (msg.event === 'state' || msg.event === 'op' || msg.event === 'rejected') {
      handler({
        kind: msg.event,
        rev: Number(d.rev ?? 0),
        text: String(d.text ?? ''),
        from: typeof d.from === 'string' ? d.from : undefined,
      });
    }
  });
}

export function collabJoin(paneKey: string): void {
  sendChannel('collab', 'join', { paneKey });
}

export function collabLeave(paneKey: string): void {
  sendChannel('collab', 'leave', { paneKey });
}

export function collabOp(paneKey: string, baseRev: number, text: string): void {
  sendChannel('collab', 'op', { paneKey, baseRev, text });
}
