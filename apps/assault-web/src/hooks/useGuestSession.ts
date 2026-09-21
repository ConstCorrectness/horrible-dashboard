import { useState, useCallback } from 'react';
import { setWsPath } from '@horrible/core';

const STORAGE_KEY = 'hassault_guest_callsign';

function generateDefaultCallsign(): string {
  const num = Math.floor(100 + Math.random() * 900);
  return `Operative-${num}`;
}

export function useGuestSession() {
  const [callsign, setCallsignState] = useState<string>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && stored.trim().length > 0) return stored.trim().slice(0, 16);
    const initial = generateDefaultCallsign();
    localStorage.setItem(STORAGE_KEY, initial);
    return initial;
  });

  const updateCallsign = useCallback((name: string) => {
    const cleaned = name.trim().slice(0, 16) || generateDefaultCallsign();
    setCallsignState(cleaned);
    localStorage.setItem(STORAGE_KEY, cleaned);
    setWsPath(`/hassault-ws?guest=1&name=${encodeURIComponent(cleaned)}`);
  }, []);

  return {
    callsign,
    setCallsign: updateCallsign,
  };
}
