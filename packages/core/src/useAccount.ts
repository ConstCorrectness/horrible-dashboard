import { useSyncExternalStore } from 'react';

import { accountStore, refreshAccount, type AccountState } from './account-store';

/**
 * Subscribe to the shared account. Every caller sees the same object, and a
 * sign-in anywhere in the app updates all of them.
 *
 * The hook is in its own file so `account-store.ts` stays importable from
 * non-React code (and from tests) without pulling React in.
 */
export function useAccount(): AccountState & { refresh: () => void } {
  const state = useSyncExternalStore(
    accountStore.subscribe,
    accountStore.getState,
    accountStore.getState,
  );
  return { ...state, refresh: () => void refreshAccount() };
}
