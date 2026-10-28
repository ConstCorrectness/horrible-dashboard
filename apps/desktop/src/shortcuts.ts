/**
 * OS-level global shortcuts — chords that fire while the app is unfocused.
 *
 * The frontend owns the keymap, so this stays a dumb registrar: it is handed a list
 * of key specs, registers exactly those, and sends `global-shortcut` back to the page
 * when one fires. Deciding *which* command a spec runs happens in
 * `packages/core/src/keymap/global.ts`, so a rebind never needs a shell change.
 */
import { globalShortcut } from 'electron';

import { toAccelerator } from './accelerator.js';

/**
 * Replace the registered set with exactly `specs`; returns the ones that took.
 *
 * Whole-set rather than incremental: the OS registration is the source of truth, and
 * a diff that drifts leaves a chord bound to a command the user has since rebound. A
 * chord another app already holds is skipped, not an error.
 */
export function registerShortcuts(specs: string[], fire: (spec: string) => void): string[] {
  globalShortcut.unregisterAll();
  const registered: string[] = [];
  for (const spec of specs) {
    const accelerator = toAccelerator(spec);
    if (!accelerator) continue;
    try {
      if (globalShortcut.register(accelerator, () => fire(spec))) registered.push(spec);
    } catch {
      // An accelerator Electron cannot parse on this platform.
    }
  }
  return registered;
}

export function unregisterAllShortcuts(): void {
  globalShortcut.unregisterAll();
}
