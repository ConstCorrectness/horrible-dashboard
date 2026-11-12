/**
 * Quick Play asks the server where to go.
 *
 * The page used to read the room list and choose for itself, and the native
 * client did the same with a different rule and a different default map — so
 * two people pressing play on an empty server at the same moment each got a
 * room to themselves. The rule now lives on the server
 * (`POST /api/hassault/matchmake`), which also opens the room when there is
 * none, before either of them has joined it.
 */

export interface Matched {
  /** The room to join, by id. */
  id: string;
  map: string;
  mode: string;
  /** Nobody was playing anything that fit: the server opened this one. */
  opened: boolean;
}

type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/**
 * The room the server chose, or `null` when it could not say — an older
 * server with no such route, a refusal, a dropped request. `null` is never an
 * error to show: the caller falls back to choosing from the list it already
 * has, which is what Quick Play did before the server could choose.
 */
export async function findMatch(
  url: string,
  want: { map?: string; mode?: string } = {},
  fetchImpl: Fetch = fetch,
): Promise<Matched | null> {
  try {
    const res = await fetchImpl(url, {
      method: 'POST',
      credentials: 'omit',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ map: want.map ?? '', mode: want.mode ?? '' }),
    });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    if (!data || typeof data !== 'object') return null;
    const { id, map, mode, opened } = data as Record<string, unknown>;
    if (typeof id !== 'string' || !id || typeof map !== 'string' || !map) return null;
    return { id, map, mode: typeof mode === 'string' ? mode : 'dm', opened: opened === true };
  } catch {
    return null;
  }
}
