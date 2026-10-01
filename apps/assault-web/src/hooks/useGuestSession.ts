import { useState, useCallback } from 'react';
import { applySocketIdentity } from '../auth';

const STORAGE_KEY = 'hassault_guest_callsign';
const GUEST_CHOSEN_KEY = 'hassault_guest_chosen';

/**
 * Whether this browser has already chosen to play as a guest. Until it has (and
 * while it is not signed in), the page opens on the sign-in gate instead of the
 * landing page. Unreadable storage counts as "not chosen": the gate is one click.
 */
export function guestChosen(): boolean {
  try {
    return localStorage.getItem(GUEST_CHOSEN_KEY) === '1';
  } catch {
    return false;
  }
}

export function chooseGuest(): void {
  try {
    localStorage.setItem(GUEST_CHOSEN_KEY, '1');
  } catch {
    /* holds for this page only */
  }
}

function generateDefaultCallsign(): string {
  const num = Math.floor(100 + Math.random() * 900);
  return `Operative-${num}`;
}

/**
 * The stored callsign, generating and storing one on first visit. `main.tsx`
 * calls this before the socket opens, so a first-time guest joins named.
 */
export function ensureCallsign(): string {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && stored.trim().length > 0) return stored.trim().slice(0, 16);
  const initial = generateDefaultCallsign();
  localStorage.setItem(STORAGE_KEY, initial);
  return initial;
}

export function useGuestSession() {
  const [callsign, setCallsignState] = useState<string>(ensureCallsign);

  const updateCallsign = useCallback((name: string) => {
    const cleaned = name.trim().slice(0, 16) || generateDefaultCallsign();
    setCallsignState(cleaned);
    localStorage.setItem(STORAGE_KEY, cleaned);
    // Only reaches the socket while signed out — a signed-in player keeps the token.
    applySocketIdentity(cleaned);
  }, []);

  return {
    callsign,
    setCallsign: updateCallsign,
  };
}
