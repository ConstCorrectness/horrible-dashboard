/**
 * The native window's titlebar. When the shell grants `chrome.workspaceTabs` the
 * window is undecorated and this slim strip is all the OS chrome it has: a drag
 * region, the shell indicators, and the min/max/close controls. In the browser
 * the page already sits inside a window, so it renders nothing.
 */
import { hasCapability } from '@horrible/core';

import { useAppFullscreen } from '../hooks/useAppFullscreen';
import { ShellIndicators } from './ShellIndicators';
import { WindowControls } from './WindowChrome';

export function TitleBar() {
  const nativeChrome = hasCapability('chrome.workspaceTabs');
  // The strip stops being a drag region while the app is fullscreen. The OS
  // treats a drag region as the window's caption, so a double-click on it
  // maximizes natively, never reaching `window_toggle_maximize` — and maximizing
  // a fullscreen frameless window is what paints the taskbar-shaped black band
  // along the bottom (see enterFullscreen in apps/desktop/src/window.ts). There
  // is nothing to drag in fullscreen anyway.
  const { fullscreen } = useAppFullscreen();
  if (!nativeChrome) return null;
  return (
    <header className="frame-tabs frame-tabs--native frame-tabs--bare">
      {/* The drag region's only content is nothing at all, so it has to be given
          the strip's full height explicitly in frame.css — otherwise it measures
          full width and zero height, and the titlebar has no grabbable pixel. */}
      <div
        className="frame-tabs-scroll"
        {...(fullscreen ? {} : { 'data-app-drag-region': '' })}
      />
      <ShellIndicators />
      <WindowControls />
    </header>
  );
}
