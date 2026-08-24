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
export const BROWSER_CAPABILITIES: Capability[] = [
  'notifications.system',
  'window.fullscreen',
  'media.displayCapture',
];

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
  'media.displayCapture',
];

/**
 * The desktop set for a specific platform.
 *
 * `DESKTOP_CAPABILITIES` is one list for three operating systems, which is fine
 * for everything driven by the shell's own code — but not for `getDisplayMedia`,
 * which WebView2 and WebKitGTK support and WKWebView does not. Granting it
 * uniformly would be the "static per-host list that has to claim one answer for
 * desktop and is wrong on one platform" that `share/capture.ts` warns about, so
 * the shell subtracts what its own platform cannot do.
 */
export function desktopCapabilities(platform: 'mac' | 'win' | 'linux'): Capability[] {
  if (platform !== 'mac') return DESKTOP_CAPABILITIES;
  return DESKTOP_CAPABILITIES.filter((c) => c !== 'media.displayCapture');
}

let active = new Set<Capability>();

/** Called once by the app entry (apps/web, apps/desktop) before rendering. */
export function initCapabilities(capabilities: Capability[]): void {
  active = new Set(capabilities);
}

export function hasCapability(capability: Capability): boolean {
  return active.has(capability);
}
