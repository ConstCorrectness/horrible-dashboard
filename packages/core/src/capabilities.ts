// The Capability union lives in @horribledashboard/sdk (shared with plugins); the
// runtime state below stays host-side.
import type { Capability } from '@horribledashboard/sdk';

export type { Capability };

// `window.fullscreen` is granted in the browser too: there is no OS window to
// drive, but the DOM Fullscreen API covers the same ground (see fullscreen.ts),
// and the capability is what gates the tray control and the `shell.toggleFullscreen`
// command. What it does NOT grant is the `f11` binding — `keymap/reserved.ts`
// declares that key browser-owned and unpreventable, so the binding is gated on
// the native seam instead.
export const BROWSER_CAPABILITIES: Capability[] = ['notifications.system', 'window.fullscreen'];

export const DESKTOP_CAPABILITIES: Capability[] = [
  'fs.nativeDialogs',
  'shell.revealInOS',
  'notifications.system',
  'window.multi',
  'window.fullscreen',
  'window.perWorkspace',
  'chrome.workspaceTabs',
  'shortcuts.global',
  'tray',
  'browser.nativeWindow',
  'browser.nativeWebview',
];

let active = new Set<Capability>();

/** Called once by the app entry (apps/web, apps/desktop) before rendering. */
export function initCapabilities(capabilities: Capability[]): void {
  active = new Set(capabilities);
}

export function hasCapability(capability: Capability): boolean {
  return active.has(capability);
}
