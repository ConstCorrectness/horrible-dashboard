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
import type { RosterSnapshot } from './api';

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
  onSocketOpen(() => sendChannel('social', 'roster', {}));
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
