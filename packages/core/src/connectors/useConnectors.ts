import { useSyncExternalStore } from 'react';

import { connectorsStore, refreshConnectors, type ConnectorsState } from './store';
import type { Connector } from './api';

/** Subscribe to the shared connector list. */
export function useConnectors(): ConnectorsState & { refresh: () => void } {
  const state = useSyncExternalStore(
    connectorsStore.subscribe,
    connectorsStore.getState,
    connectorsStore.getState,
  );
  return { ...state, refresh: () => void refreshConnectors() };
}

/**
 * Subscribe to one connector by id.
 *
 * `connected` is deliberately `false` until proven otherwise, while `known` says
 * whether that answer means anything yet. A gate that hid its pane on `!connected`
 * alone would flash "connect GitHub" at an already-connected user on every mount.
 */
export function useConnector(id: string): {
  connector: Connector | undefined;
  connected: boolean;
  known: boolean;
} {
  const { connectors, phase } = useConnectors();
  const connector = connectors.find((c) => c.id === id);
  return {
    connector,
    connected: connector?.connected === true,
    known: phase === 'ready',
  };
}
