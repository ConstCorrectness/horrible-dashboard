/**
 * The live sessions this machine is invited to, kept current from the `/ws` events
 * the relay sends (backend/modules/scrive/live.py). Separate from `session.ts` so the
 * shell's indicator can watch for invitations without loading Yjs at boot.
 */
import { onSocketOpen, sendChannel, subscribeChannel } from '../../../ws';

export const LIVE_CHANNEL = 'scrive-live';

export interface LiveSummary {
  key: string;
  role: 'host' | 'guest';
  site: string;
  path: string;
  title: string;
  /** The host's name, on a guest's side. */
  host: string;
}

// --- invitations (this machine as a guest) ------------------------------------------

let invitations: LiveSummary[] = [];
const inviteListeners = new Set<() => void>();
let watching = false;

export function getInvitations(): LiveSummary[] {
  return invitations;
}

/** Keep the list of sessions this machine is invited to; `onInvite` fires for a new one. */
export function watchInvitations(onInvite: (s: LiveSummary) => void): void {
  if (watching) return;
  watching = true;
  const set = (next: LiveSummary[]) => {
    invitations = next;
    inviteListeners.forEach((l) => l());
  };
  subscribeChannel(LIVE_CHANNEL, (msg) => {
    if (msg.event === 'invitations')
      set(((msg.data as { rooms?: LiveSummary[] })?.rooms ?? []).filter((r) => r.role === 'guest'));
  });
  subscribeChannel('scrive', (msg) => {
    if (msg.event === 'live.invited') {
      const room = msg.data as LiveSummary;
      if (!invitations.some((r) => r.key === room.key)) onInvite(room);
      set([...invitations.filter((r) => r.key !== room.key), room]);
    } else if (msg.event === 'live.ended') {
      const key = (msg.data as { key?: string })?.key;
      set(invitations.filter((r) => r.key !== key));
    }
  });
  onSocketOpen(() => sendChannel(LIVE_CHANNEL, 'invitations', {}));
}

export function subscribeInvitations(listener: () => void): () => void {
  inviteListeners.add(listener);
  return () => {
    inviteListeners.delete(listener);
  };
}
