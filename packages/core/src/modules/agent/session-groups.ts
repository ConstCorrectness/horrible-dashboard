/**
 * The sessions rail's recency buckets (SessionsRail.tsx). Pure, so the calendar
 * boundaries are testable without a DOM.
 *
 * Buckets are by **local calendar day**, not by 24-hour windows: a chat from 23:50
 * last night is "Yesterday" at 00:10, which is how a reader thinks of it. `updated`
 * is the backend's `time.time()` — epoch **seconds**, not milliseconds.
 */
import type { ChatSessionMeta } from './sessions';

export type SessionGroupLabel =
  | 'Today'
  | 'Yesterday'
  | 'Previous 7 days'
  | 'Previous 30 days'
  | 'Older';

export interface SessionGroup {
  label: SessionGroupLabel;
  sessions: ChatSessionMeta[];
}


/** Local midnight at the start of `t`'s day. */
function startOfDay(t: number): number {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function labelFor(updatedMs: number, today: number): SessionGroupLabel {
  if (updatedMs >= today) return 'Today';
  // Subtracting a day of ms from local midnight is off by an hour across a DST
  // change; `setDate` keeps it on the calendar.
  const day = (offset: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() - offset);
    return d.getTime();
  };
  if (updatedMs >= day(1)) return 'Yesterday';
  if (updatedMs >= day(7)) return 'Previous 7 days';
  if (updatedMs >= day(30)) return 'Previous 30 days';
  return 'Older';
}

/** Sessions newest first, bucketed by recency; empty buckets are left out. */
export function groupSessions(
  sessions: ChatSessionMeta[],
  now: number = Date.now(),
): SessionGroup[] {
  const today = startOfDay(now);
  const sorted = [...sessions].sort((a, b) => b.updated - a.updated);
  const groups: SessionGroup[] = [];
  for (const s of sorted) {
    const label = labelFor(s.updated * 1000, today);
    const last = groups.at(-1);
    if (last?.label === label) last.sessions.push(s);
    else groups.push({ label, sessions: [s] });
  }
  return groups;
}

/** A short age for a rail row: `now`, `5m`, `3h`, `2d`, `6w`, `1y`. */
export function shortAge(updatedSec: number, now: number = Date.now()): string {
  const s = Math.max(0, now / 1000 - updatedSec);
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h`;
  if (s < 7 * 86_400) return `${Math.floor(s / 86_400)}d`;
  if (s < 365 * 86_400) return `${Math.floor(s / (7 * 86_400))}w`;
  return `${Math.floor(s / (365 * 86_400))}y`;
}

/** Case-insensitive title filter; a blank query keeps everything. */
export function filterSessions(sessions: ChatSessionMeta[], query: string): ChatSessionMeta[] {
  const q = query.trim().toLowerCase();
  if (!q) return sessions;
  return sessions.filter((s) => s.title.toLowerCase().includes(q));
}

