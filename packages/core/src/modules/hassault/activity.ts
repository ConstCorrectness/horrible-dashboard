/**
 * What your friends see you doing while you are in a HorribleAssault match: the
 * "In-Game" line of their friends list.
 *
 * Reported from the session's own change stream, deduplicated here on the text
 * friends would read, so the per-tick `emit()` costs a string compare and a
 * message goes out only when the line actually changes — a map change, a mode, a
 * new score.
 */
import { setActivity } from '../social/ws';
import type { SessionState } from './session';

export const GAME_TITLE = 'HorribleAssault';

/** The activity line for a session state, or null when not in a match. */
export function activityDetail(state: SessionState): string | null {
  if (state.status !== 'joined') return null;
  const parts = [state.map || 'in a match'];
  if (state.mode?.name) parts.push(state.mode.name);
  if (state.ranked) parts.push('Ranked');
  else if (state.host) parts.push('with friends');
  parts.push(`${state.scores[0]}–${state.scores[1]}`);
  return parts.join(' · ');
}

let last: string | null = null;

export function reportActivity(state: SessionState): void {
  // `MatchSession` also runs outside a page — its unit tests drive it under node —
  // and there is no `/ws` to report to there; opening one would throw.
  if (typeof window === 'undefined') return;
  const detail = activityDetail(state);
  if (detail === last) return;
  last = detail;
  setActivity(detail === null ? null : { kind: 'game', title: GAME_TITLE, detail });
}
