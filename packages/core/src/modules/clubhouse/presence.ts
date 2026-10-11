/**
 * Whether this app is in a Clubhouse room, readable without loading the room.
 *
 * The room itself (`roomSession.ts`) drags in the Agora and PubNub SDKs, which is
 * why the pane is a `lazyPane`. The shell indicator has to render at boot and on
 * every desktop, so it cannot import that — it reads this instead. The session
 * publishes here on every change, and registers how to leave.
 *
 * It exists because a call used to be able to play with nothing on screen
 * owning it: the room lived in a pane on another desktop, behind a hidden dock or
 * in a desktop board tile, and closing the Clubhouse window in front of you did
 * not touch it. The only way out was closing the whole app.
 */

export interface ClubhousePresence {
  joined: boolean;
  /** Joining or leaving is in flight. */
  loading: boolean;
  /** People the room session knows about, the operator included. */
  people: number;
  muted: boolean;
}

const IDLE: ClubhousePresence = { joined: false, loading: false, people: 0, muted: false };

let current: ClubhousePresence = IDLE;
let leaver: (() => Promise<void>) | null = null;
const listeners = new Set<() => void>();

export function getClubhousePresence(): ClubhousePresence {
  return current;
}

export function subscribeClubhousePresence(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Called by the room session. Skips no-op updates so the shell does not re-render
 *  twenty times a second while the volume meter churns the room state. */
export function publishClubhousePresence(next: ClubhousePresence): void {
  if (
    next.joined === current.joined &&
    next.loading === current.loading &&
    next.people === current.people &&
    next.muted === current.muted
  ) {
    return;
  }
  current = next;
  for (const listener of listeners) listener();
}

/** Registered by the room session when it is created. */
export function setClubhouseLeaver(fn: (() => Promise<void>) | null): void {
  leaver = fn;
}

/** Leave whatever room this app is in. A no-op when the room was never loaded. */
export function leaveClubhouse(): Promise<void> {
  return leaver ? leaver() : Promise.resolve();
}

/** Test-only. */
export function resetClubhousePresenceForTests(): void {
  current = IDLE;
  leaver = null;
  listeners.clear();
}
