import { useState, useEffect, useCallback } from 'react';
import { apiUrl } from '@horrible/core';

export interface GameRoom {
  id: string;
  map: string;
  playerCount: number;
  maxPlayers: number;
  mode: string;
  hasGuests?: boolean;
  rated?: boolean;
}

export function useGameRooms() {
  const [rooms, setRooms] = useState<GameRoom[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pingMs, setPingMs] = useState<number | null>(null);

  const fetchRooms = useCallback(async () => {
    const start = performance.now();
    try {
      const res = await fetch(apiUrl('/api/hassault/rooms'), { credentials: 'omit' });
      const elapsed = Math.round(performance.now() - start);
      setPingMs(elapsed);
      if (!res.ok) {
        throw new Error(`Failed to fetch rooms: HTTP ${res.status}`);
      }
      const data = await res.json();
      setRooms(Array.isArray(data?.rooms) ? data.rooms : []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchRooms();
    const interval = setInterval(() => {
      void fetchRooms();
    }, 3000);
    return () => clearInterval(interval);
  }, [fetchRooms]);

  return {
    rooms,
    loading,
    error,
    pingMs,
    refresh: fetchRooms,
  };
}
