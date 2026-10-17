/**
 * Client for the `/ws` `social` channel: keeps a live roster snapshot the Friends
 * panel renders. Presence is pushed, not polled — a friend's machine connecting or
 * dropping is a fabric event, and the panel should reflect it at once.
 *
 * Same shape as the network module's store: one shared snapshot, re-synced on every
 * socket (re)connect.
 */
import { onSocketOpen, sendChannel, subscribeChannel } from '../../ws';
import { toastsStore } from '../../toasts';
import type { Activity, Persona, RosterSnapshot } from './api';

export interface SocialState {
  roster: RosterSnapshot | null;
}

let state: SocialState = { roster: null };
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

export function getSocialState(): SocialState {
  return state;
}

export function subscribeSocial(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

let started = false;

export function initSocial(): void {
  if (started) return;
  started = true;
  subscribeChannel('social', (msg) => {
    if (msg.event === 'roster') {
      state = { roster: msg.data as RosterSnapshot };
      emit();
    } else if (msg.event === 'friend_request') {
      const d = msg.data as { display_name: string };
      toastsStore.add('info', 'Friend request', `${d.display_name} wants to be friends.`);
    }
  });
  onSocketOpen(() => {
    sendChannel('social', 'roster', {});
    // A restarted backend forgets idleness; say it again rather than showing a
    // friend `online` for someone who left an hour ago.
    if (idle) sendChannel('social', 'status', { idle: true });
  });
  startIdleWatch();
}

export function requestRoster(): void {
  sendChannel('social', 'roster', {});
}

export function respondViaChannel(personId: string, accept: boolean): void {
  sendChannel('social', 'respond', { personId, accept });
}

export function removeViaChannel(personId: string): void {
  sendChannel('social', 'remove', { personId });
}

export function blockViaChannel(personId: string): void {
  sendChannel('social', 'block', { personId });
}

/** Set your persona — Online, Away, Do Not Disturb (`busy`) or Invisible. */
export function setPersona(persona: Persona): void {
  sendChannel('social', 'status', { persona });
}

/**
 * Report what you are doing, or `null` when you stop. Safe to call on every render:
 * the backend keeps the original start time for a repeated title, and sends
 * friends nothing when nothing they can see changed.
 */
export function setActivity(
  activity:
    | (Omit<Activity, 'since' | 'detail'> & {
        detail?: string | null;
      })
    | null,
): void {
  if (activity === null) sendChannel('social', 'status', { clearActivity: true });
  else sendChannel('social', 'status', { activity });
}

// ---- idle → away -----------------------------------------------------------------

/** Steam's auto-away: ten minutes with no input in any window of the app. */
const IDLE_AFTER_MS = 10 * 60 * 1000;
let idleTimer: number | undefined;
let idle = false;

function markActive(): void {
  if (idle) {
    idle = false;
    sendChannel('social', 'status', { idle: false });
  }
  window.clearTimeout(idleTimer);
  idleTimer = window.setTimeout(() => {
    idle = true;
    sendChannel('social', 'status', { idle: true });
  }, IDLE_AFTER_MS);
}

/**
 * Watch for input, and report idleness as a *flag* rather than a persona: the
 * backend turns a chosen `online` into a shared `away` while it is set, and leaves
 * `busy` and `invisible` alone, so coming back has nothing to undo.
 */
function startIdleWatch(): void {
  if (typeof window === 'undefined') return;
  let last = 0;
  const onInput = () => {
    // Re-arming the timer on every mousemove is wasted work; once a second is
    // plenty for a ten-minute threshold.
    const now = Date.now();
    if (!idle && now - last < 1000) return;
    last = now;
    markActive();
  };
  for (const type of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'focus']) {
    window.addEventListener(type, onInput, { passive: true, capture: true });
  }
  markActive();
}
