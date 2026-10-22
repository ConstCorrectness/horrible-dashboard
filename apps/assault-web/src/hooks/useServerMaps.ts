import { useEffect, useState } from 'react';
import { apiUrl } from '@horrible/core';

/** What the game server says about its maps (`GET /hassault/maps`). */
export interface ServerMaps {
  maps: string[];
  /** The map every ranked duel is fought on; loaded before queueing. */
  duelMap: string;
  /** The modes a room on each map may open in. */
  modes: Record<string, string[]>;
}

/**
 * Fetched once per mount. `null` until it arrives, and on failure, so callers
 * fall back to what the page knows (`modesFor`) rather than offering nothing.
 */
export function useServerMaps(): ServerMaps | null {
  const [served, setServed] = useState<ServerMaps | null>(null);
  useEffect(() => {
    let live = true;
    fetch(apiUrl('/hassault/maps'), { credentials: 'omit' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!live || !data) return;
        setServed({
          maps: Array.isArray(data.maps) ? data.maps : [],
          duelMap: typeof data.duelMap === 'string' ? data.duelMap : '',
          modes: data.modes && typeof data.modes === 'object' ? data.modes : {},
        });
      })
      .catch(() => {
        /* the menu falls back to the page's own map table */
      });
    return () => {
      live = false;
    };
  }, []);
  return served;
}
