/**
 * Recently opened panes.
 *
 * The Start menu is this app's primary navigation, and it was a directory
 * listing: 51 openers under 38 headings, alphabetical by module id, with no
 * memory of anything. A launcher that cannot remember what you opened five
 * minutes ago makes you re-navigate the whole taxonomy every time, and the panes
 * a person actually uses are a handful out of the fifty-one.
 *
 * Kept in `localStorage`, deliberately, not in settings: it is a per-device
 * convenience with no meaning on another machine, it changes on every pane open
 * (a settings PUT per launch would be absurd), and `GET /api/settings` hands the
 * whole bag to the browser — this does not belong in it. Same reasoning as the
 * per-workspace agent overrides next door in `persistence.ts`.
 *
 * View *ids* are stored, never titles or components: a stored title would drift
 * from the registry the moment a pane was renamed, and an id that no longer
 * resolves is simply dropped on read.
 */

const KEY = 'horrible.recentViews';

/**
 * How many to remember.
 *
 * Six, because the band is meant to be read at a glance above the grouped list —
 * Miller's limit is the constraint, not storage. A "recent" list long enough to
 * need scanning is just a second copy of the menu.
 */
export const MAX_RECENTS = 6;

function read(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    // A corrupt or unavailable store must not take the launcher down with it.
    return [];
  }
}

const listeners = new Set<() => void>();
let cache: string[] | null = null;

/** The recent view ids, most recent first. */
export function recentViewIds(): string[] {
  cache ??= read();
  return cache;
}

/**
 * Record that a view was opened.
 *
 * Moves an existing entry to the front rather than adding a duplicate, so the
 * list is an ordering of distinct panes and not an event log.
 */
export function noteViewOpened(viewId: string): void {
  const next = [viewId, ...recentViewIds().filter((id) => id !== viewId)].slice(0, MAX_RECENTS);
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Private mode, quota, or no storage at all. The in-memory list still works
    // for this session; losing it on reload is not worth failing an open over.
  }
  for (const l of listeners) l();
}

export function subscribeRecents(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test seam. */
export function clearRecents(): void {
  cache = [];
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
  for (const l of listeners) l();
}
