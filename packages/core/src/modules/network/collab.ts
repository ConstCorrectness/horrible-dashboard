/**
 * Client for the `/ws` `collab` channel: shared-pane state sync. Thin primitives a
 * pane uses to join a room, push edits (with its last-known revision), and receive
 * authoritative state. Conflict handling is rev-checked last-writer-wins on the
 * backend (see modules/network collab.py) — this is the groundwork a real CRDT
 * would replace.
 */
import { sendChannel, subscribeChannel } from '../../ws';

export interface CollabUpdate {
  kind: 'state' | 'op' | 'rejected' | 'presence' | 'shared' | 'error';
  rev: number;
  text: string;
  from?: string;
  /** Live occupancy of the room (set on `state`/`presence`). */
  members?: number;
  /** Who this pane is shared with, by person (set on `shared`). */
  people?: { personId: string; name: string }[];
  error?: string;
}

/** Subscribe to updates for one shared pane. Returns an unsubscribe function. */
export function subscribeCollab(
  paneKey: string,
  handler: (update: CollabUpdate) => void,
): () => void {
  return subscribeChannel('collab', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    if (d.paneKey !== paneKey) return;
    if (msg.event === 'presence') {
      handler({ kind: 'presence', rev: 0, text: '', members: Number(d.members ?? 0) });
    } else if (msg.event === 'shared') {
      handler({
        kind: 'shared',
        rev: 0,
        text: '',
        people: (d.people as { personId: string; name: string }[]) ?? [],
      });
    } else if (msg.event === 'error') {
      handler({ kind: 'error', rev: 0, text: '', error: String(d.message ?? '') });
    } else if (msg.event === 'state' || msg.event === 'op' || msg.event === 'rejected') {
      handler({
        kind: msg.event,
        rev: Number(d.rev ?? 0),
        text: String(d.text ?? ''),
        from: typeof d.from === 'string' ? d.from : undefined,
        members: d.members !== undefined ? Number(d.members) : undefined,
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

/**
 * Share this pane with a **person**.
 *
 * Addressed by `person_id`, never by node: the fabric picks whichever of their
 * machines is online. Asking someone which of their own computers to send a pane
 * to is a question with no good answer — and it is the same reason the roster is
 * keyed by person rather than by machine.
 *
 * Before this there was no share call at all. Every accepted op was forwarded to
 * every connected peer, so "sharing" was a global broadcast and there was nothing
 * to address.
 */
export function collabShare(paneKey: string, personId: string): void {
  sendChannel('collab', 'share', { paneKey, personId });
}

export function collabUnshare(paneKey: string, personId: string): void {
  sendChannel('collab', 'unshare', { paneKey, personId });
}
