/** Open/close state for the global symbol-search quick-open modal. A tiny store so a
 * command (`code.findSymbol`) + keybinding can toggle it and AppShell can render it,
 * without the shell owning the state. See docs/modules/code.mdx. */
import { useSyncExternalStore } from 'react';

let open = false;
const listeners = new Set<() => void>();

export const symbolSearchModal = {
  isOpen: (): boolean => open,
  set: (value: boolean): void => {
    if (open === value) return;
    open = value;
    listeners.forEach((l) => l());
  },
  toggle: (): void => symbolSearchModal.set(!open),
  subscribe: (l: () => void): (() => void) => {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
};

export function useSymbolSearchModalOpen(): boolean {
  return useSyncExternalStore(
    symbolSearchModal.subscribe,
    symbolSearchModal.isOpen,
    symbolSearchModal.isOpen,
  );
}
