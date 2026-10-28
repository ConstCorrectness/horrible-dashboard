/**
 * Electron-backed GlobalShortcuts - OS accelerators that fire while the app
 * is unfocused. Injected into the core seam at boot under Electron desktop shell.
 */
import type { GlobalShortcuts } from '@horrible/core';

interface HorriblePlatformWithShortcuts {
  shortcuts: {
    register: (accelerators: string[]) => Promise<string[]>;
    unregisterAll: () => Promise<void>;
    onTrigger: (listener: (accelerator: string) => void) => () => void;
  };
}

export function createElectronGlobalShortcuts(): GlobalShortcuts {
  let unlisten: (() => void) | null = null;
  let handler: (accelerator: string) => void = () => {};

  return {
    async register(accelerators, onTrigger) {
      const platform = (window as unknown as { horriblePlatform?: HorriblePlatformWithShortcuts })
        .horriblePlatform;
      if (!platform?.shortcuts) return;

      handler = onTrigger;
      if (!unlisten) {
        unlisten = platform.shortcuts.onTrigger((accel) => {
          handler(accel);
        });
      }
      await platform.shortcuts.register(accelerators);
    },
    async unregisterAll() {
      const platform = (window as unknown as { horriblePlatform?: HorriblePlatformWithShortcuts })
        .horriblePlatform;
      if (platform?.shortcuts) {
        await platform.shortcuts.unregisterAll();
      }
      unlisten?.();
      unlisten = null;
    },
  };
}
