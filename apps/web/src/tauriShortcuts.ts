/**
 * Tauri-backed {@link GlobalShortcuts} — OS accelerators that fire while the app
 * is unfocused. Injected into the core seam at boot under Tauri; in the browser
 * the seam stays null and `shortcuts.global` is ungranted. The dynamic imports
 * keep `@tauri-apps/api` out of the browser bundle (same pattern as
 * tauriWindow.ts).
 */
import type { GlobalShortcuts } from '@horrible/core';

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd, args);
}

export function createTauriGlobalShortcuts(): GlobalShortcuts {
  // One event subscription for the life of the app: re-registering accelerators
  // must not stack up listeners, or a single press would run its command once
  // per registration since boot.
  let unlisten: (() => void) | null = null;
  let handler: (accelerator: string) => void = () => {};

  return {
    async register(accelerators, onTrigger) {
      handler = onTrigger;
      if (!unlisten) {
        const { listen } = await import('@tauri-apps/api/event');
        unlisten = await listen<{ accelerator: string }>('global-shortcut', (event) => {
          handler(event.payload.accelerator);
        });
      }
      await invoke<string[]>('shortcuts_register', { accelerators });
    },
    async unregisterAll() {
      await invoke<void>('shortcuts_unregister_all');
      unlisten?.();
      unlisten = null;
    },
  };
}
