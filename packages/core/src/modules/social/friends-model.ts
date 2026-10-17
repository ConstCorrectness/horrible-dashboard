/**
 * How the Friends list sorts and words people. Pure, so it is tested without a DOM.
 *
 * Steam's groups, in Steam's order, because each answers a different question at a
 * glance: **Friend Requests** (is anything waiting on me), **In-Game** (who is
 * doing something I could join), **Online** (who could I talk to), **Offline**,
 * and the requests you sent, folded at the bottom where they do not crowd anyone
 * who actually answered.
 */
import type { Friend, Persona, SharedPersona } from './api';

export type GroupId = 'requests' | 'ingame' | 'online' | 'offline' | 'sent';

export interface FriendGroup {
  id: GroupId;
  label: string;
  friends: Friend[];
}

/** The colour a row's persona takes — `data-persona` in the stylesheet. */
export type PersonaTone = 'ingame' | 'online' | 'away' | 'busy' | 'offline';

const RANK: Record<SharedPersona, number> = { online: 0, busy: 1, away: 2, offline: 3 };

const LABEL: Record<Persona | SharedPersona, string> = {
  online: 'Online',
  away: 'Away',
  busy: 'Do Not Disturb',
  invisible: 'Invisible',
  offline: 'Offline',
};

export function personaLabel(persona: Persona | SharedPersona): string {
  return LABEL[persona];
}

export function inGame(friend: Friend): boolean {
  return friend.presence === 'online' && friend.activity?.kind === 'game';
}

export function personaTone(friend: Friend): PersonaTone {
  if (friend.presence !== 'online') return 'offline';
  if (inGame(friend)) return 'ingame';
  return friend.persona === 'offline' ? 'offline' : friend.persona;
}

function matches(friend: Friend, needle: string): boolean {
  if (!needle) return true;
  return (
    friend.display_name.toLowerCase().includes(needle) ||
    (friend.handle ?? '').toLowerCase().includes(needle)
  );
}

const byName = (a: Friend, b: Friend) =>
  a.display_name.localeCompare(b.display_name, undefined, { sensitivity: 'base' });

/**
 * The roster as groups, filtered by `query`. Empty groups are dropped — except
 * Online, whose "(0)" is itself the answer to "is anyone around".
 */
export function groupFriends(friends: Friend[], query = ''): FriendGroup[] {
  const needle = query.trim().toLowerCase();
  const shown = friends.filter((f) => f.status !== 'blocked' && matches(f, needle));
  const accepted = shown.filter((f) => f.status === 'accepted');

  const groups: FriendGroup[] = [
    {
      id: 'requests',
      label: 'Friend Requests',
      friends: shown.filter((f) => f.status === 'pending_in').sort(byName),
    },
    {
      id: 'ingame',
      label: 'In-Game',
      // Same game together, so a party reads as a party.
      friends: accepted
        .filter(inGame)
        .sort(
          (a, b) =>
            (a.activity?.title ?? '').localeCompare(b.activity?.title ?? '') || byName(a, b),
        ),
    },
    {
      id: 'online',
      label: 'Online',
      // Reachable first: someone on Do Not Disturb or Away is online but asked
      // for less, so they sort below the people who did not.
      friends: accepted
        .filter((f) => f.presence === 'online' && !inGame(f))
        .sort((a, b) => RANK[a.persona] - RANK[b.persona] || byName(a, b)),
    },
    {
      id: 'offline',
      label: 'Offline',
      friends: accepted.filter((f) => f.presence !== 'online').sort(byName),
    },
    {
      id: 'sent',
      label: 'Sent Requests',
      friends: shown.filter((f) => f.status === 'pending_out').sort(byName),
    },
  ];
  return groups.filter((g) => g.friends.length > 0 || g.id === 'online');
}

/** `12 min`, `2 h 5 min`, `3 d` — how long, in the fewest words that are true. */
export function durationSince(since: number, now: number): string {
  const s = Math.max(0, now - since);
  const m = Math.floor(s / 60);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  return `${Math.floor(h / 24)} d`;
}

function lastSeen(friend: Friend): number | null {
  const seen = friend.devices.map((d) => d.last_seen ?? 0).filter((t) => t > 0);
  return seen.length ? Math.max(...seen) : null;
}

/**
 * The line under a name: what they are doing, or the state they set, or when they
 * were last around. One line, because a row that wraps is a row you have to read.
 */
export function statusLine(friend: Friend, now: number): string {
  if (friend.status === 'pending_in') return 'Wants to be friends';
  if (friend.status === 'pending_out') return 'Request sent';
  if (friend.presence === 'online') {
    if (friend.activity) {
      return friend.activity.detail
        ? `${friend.activity.title} — ${friend.activity.detail}`
        : friend.activity.title;
    }
    if (friend.persona === 'online') {
      const up = friend.devices.filter((d) => d.online);
      return up.length > 1 ? `Online on ${up.length} machines` : 'Online';
    }
    return personaLabel(friend.persona);
  }
  const seen = lastSeen(friend);
  return seen ? `Last online ${durationSince(seen, now)} ago` : 'Offline';
}
