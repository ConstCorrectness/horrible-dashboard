/**
 * OS-level **global shortcuts** — bindings that fire while the app is unfocused.
 *
 * A seam, not an implementation, for the same reason `window.ts` is one: the
 * Tauri wiring belongs to the app entry, and `packages/` stays platform-agnostic.
 * The browser leaves the seam null, which is what the `shortcuts.global`
 * capability has always described but nothing implemented — before this there was
 * no global-shortcut plugin in `src-tauri` at all.
 *
 * See docs/architecture/keybindings.mdx.
 */
import { hasCapability } from '../capabilities';
import { registry } from '../registry';
import { getKeymap, keymapStore } from './state';

export interface GlobalShortcuts {
  /**
   * Replace the registered set with exactly `accelerators`. Whole-set rather than
   * add/remove, because the OS registration is the source of truth and a diff
   * that drifts leaves a chord bound to a command the user has since rebound.
   */
  register(accelerators: string[], onTrigger: (accelerator: string) => void): Promise<void>;
  unregisterAll(): Promise<void>;
}

let impl: GlobalShortcuts | null = null;

export function setGlobalShortcuts(next: GlobalShortcuts | null): void {
  impl = next;
}

export function globalShortcuts(): GlobalShortcuts | null {
  return impl;
}

/**
 * Push every `global: true` binding to the OS, and re-push whenever the keymap
 * changes (a module registers, the user rebinds). Returns an unsubscribe.
 *
 * No-op without the `shortcuts.global` capability, so the browser build calls
 * this harmlessly.
 */
export function installGlobalShortcuts(): () => void {
  if (!hasCapability('shortcuts.global')) return () => {};

  let current = '';
  const sync = () => {
    const control = globalShortcuts();
    if (!control) return;
    const globals = getKeymap().filter((b) => b.global);
    // Sequences cannot be OS accelerators — the OS grabs one chord, not a
    // prefix — so only single-stroke bindings are eligible.
    const single = globals.filter((b) => b.chord.length === 1);
    const byAccelerator = new Map(single.map((b) => [b.key, b.command]));
    const accelerators = [...byAccelerator.keys()].sort();
    const signature = accelerators.join('|');
    if (signature === current) return;
    current = signature;
    void control.register(accelerators, (accelerator) => {
      const command = byAccelerator.get(accelerator);
      if (command) void registry.runCommand(command);
    });
  };

  sync();
  const off = keymapStore.subscribe(sync);
  return () => {
    off();
    void globalShortcuts()?.unregisterAll();
  };
}
