/**
 * Desktop-shell {@link GlobalShortcuts} — OS accelerators that fire while the app is
 * unfocused. Injected into the core seam at boot under the shell; in the browser the
 * seam stays null and `shortcuts.global` is ungranted.
 */
import { desktopBridge, desktopInvoke, type GlobalShortcuts } from '@horrible/core';

export function createDesktopGlobalShortcuts(): GlobalShortcuts {
  // One event subscription for the life of the app: re-registering accelerators
  // must not stack up listeners, or a single press would run its command once
  // per registration since boot.
  let unlisten: (() => void) | null = null;
  let handler: (accelerator: string) => void = () => {};

  return {
    async register(accelerators, onTrigger) {
      handler = onTrigger;
      unlisten ??=
        desktopBridge()?.listen('global-shortcut', (payload) => {
          handler((payload as { accelerator: string }).accelerator);
        }) ?? null;
      await desktopInvoke<string[]>('shortcuts_register', { accelerators });
    },
    async unregisterAll() {
      await desktopInvoke<void>('shortcuts_unregister_all');
      unlisten?.();
      unlisten = null;
    },
  };
}
