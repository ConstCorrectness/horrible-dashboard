/**
 * One invite, four surfaces, one key.
 *
 * A match invitation is shown in more places than anything else in the app: a
 * shell toast with a Join button, a row in the notification feed, a card over the
 * canvas while you are playing (pointer lock hides the shell, so the toast is
 * invisible there), and an OS notification when the window is not focused. That
 * is deliberate — an invite you do not see in time is an invite to a match that
 * has already started without you.
 *
 * The cost of four surfaces is that answering has to reach all four. Without a
 * shared key, joining from the game leaves the toast, the bell and the OS
 * notification sitting there unanswered, and a re-sent invite stacks a second
 * identical card under the first instead of refreshing it. So the key is built in
 * exactly one place — here — and the backend builds the identical string in
 * `fabric._notify_invite`. Two spellings of it would be a bug that looks like
 * nothing: everything still works, notifications just never clear.
 *
 * Keyed by `(host node, room)` rather than by person: one friend can be running
 * two matches, and answering one of them says nothing about the other.
 *
 * See docs/modules/hassault.mdx.
 */
import { apiPost } from '../../api';
import { retractNotification } from '../notifications';

export function inviteDedupeKey(host: string, room: string): string {
  return `hassault-invite:${host}:${room}`;
}

/**
 * Retire an invite everywhere it is showing. Call on accept **and** on decline —
 * both are answers, and an invite that stays in the bell after you declined it is
 * the same defect as one that stays after you joined.
 *
 * Local first, then the server: the person clicked, so the card should go now
 * rather than after a round-trip, and the broadcast is what reaches this node's
 * other surfaces and its other browser tabs.
 */
export function clearInviteNotification(host: string, room: string): void {
  const dedupe = inviteDedupeKey(host, room);
  retractNotification(dedupe);
  void apiPost('/notifications/clear', { dedupe }).catch(() => {
    /* The row expires with the invite's TTL anyway; a failed clear costs a stale
       card for a few minutes, never a lost invite. */
  });
}

/**
 * An invite accepted from outside the game, waiting for the game to exist.
 *
 * The Join button on a shell toast can be pressed with the pane closed — which is
 * the entire point of moving invites onto the shell's notification channel, since
 * a pane that is not mounted was the thing not hearing them. But the join itself
 * needs a `MatchSession`, and that is owned by the panel and created on mount.
 *
 * So the action opens the pane and parks the intent here, and the panel picks it
 * up once it has a session and a loaded world. One slot, not a queue: pressing
 * Join twice means you want the second one, and two queued joins would have the
 * first immediately kicked out by the second anyway (`join` is idempotent by
 * leaving whatever you were in).
 */
export interface PendingJoin {
  room: string;
  map: string;
  host: string;
}

let pending: PendingJoin | null = null;
const waiting = new Set<(join: PendingJoin) => void>();

export function requestJoin(join: PendingJoin): void {
  pending = join;
  for (const listener of waiting) listener(join);
}

/** Take the parked intent, if there is one. Consuming clears it, so a later
 * remount does not re-join a match you have since left. */
export function takePendingJoin(): PendingJoin | null {
  const out = pending;
  pending = null;
  return out;
}

export function onJoinRequested(listener: (join: PendingJoin) => void): () => void {
  waiting.add(listener);
  return () => {
    waiting.delete(listener);
  };
}
