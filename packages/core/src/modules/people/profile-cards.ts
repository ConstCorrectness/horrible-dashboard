/**
 * A cache of profile cards (face, level, status) keyed by callsign.
 *
 * The roster is local and always renders; a card is decoration fetched from the
 * game server, so **every consumer must work without one**. A friend with no
 * callsign, a signed-out node and an unreachable game server are all the same case
 * here — no card, render the name and the presence dot, carry on. That is why this
 * is a separate store rather than fields on `Friend`: the roster must never
 * acquire a network dependency to draw.
 *
 * One batched request per set of handles, cached for the session. Cards change
 * rarely (someone changes their picture, someone levels up) and a stale avatar for
 * a few minutes costs nothing, so there is no polling.
 */
import { fetchProfileCards, type ProfileCard } from '../games/profile-api';

let cards: Record<string, ProfileCard> = {};
const asked = new Set<string>();
const listeners = new Set<() => void>();
let inFlight: Promise<void> | null = null;

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeProfileCards(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getProfileCards(): Record<string, ProfileCard> {
  return cards;
}

export function getProfileCard(handle: string | null | undefined): ProfileCard | null {
  if (!handle) return null;
  return cards[handle.toLowerCase()] ?? null;
}

/**
 * Fetch cards for any of these handles not already asked about.
 *
 * A handle that comes back empty is still marked asked: a friend who has never
 * opened the Plaza would otherwise be re-requested on every render forever.
 */
export async function ensureProfileCards(handles: (string | null | undefined)[]): Promise<void> {
  const wanted = handles
    .filter((h): h is string => Boolean(h))
    .map((h) => h.toLowerCase())
    .filter((h) => !asked.has(h));
  if (wanted.length === 0) return;
  for (const handle of wanted) asked.add(handle);
  const request = fetchProfileCards(wanted)
    .then((fetched) => {
      if (Object.keys(fetched).length === 0) return;
      cards = { ...cards, ...fetched };
      emit();
    })
    .catch(() => {
      // The game server being down is not an error state for the roster — it just
      // means no faces this session. Let them be re-asked later.
      for (const handle of wanted) asked.delete(handle);
    })
    .finally(() => {
      inFlight = null;
    });
  inFlight = request;
  return request;
}

/** Drop the cache — after editing your own profile, so your new picture shows. */
export function invalidateProfileCards(): void {
  cards = {};
  asked.clear();
  emit();
}

/** Test/diagnostic hook: whether a fetch is outstanding. */
export function profileCardsPending(): boolean {
  return inFlight !== null;
}
