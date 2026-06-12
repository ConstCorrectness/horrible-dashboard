// The Capability union lives in @horrible/sdk (shared with plugins); the
// runtime state below stays host-side.
import type { Capability } from '@horrible/sdk';

export type { Capability };

export const BROWSER_CAPABILITIES: Capability[] = ['notifications.system'];

export const DESKTOP_CAPABILITIES: Capability[] = [
  'fs.nativeDialogs',
  'shell.revealInOS',
  'notifications.system',
  'window.multi',
  'shortcuts.global',
  'tray',
];

let active = new Set<Capability>();

/** Called once by the app entry (apps/web, apps/desktop) before rendering. */
export function initCapabilities(capabilities: Capability[]): void {
  active = new Set(capabilities);
}

export function hasCapability(capability: Capability): boolean {
  return active.has(capability);
}
