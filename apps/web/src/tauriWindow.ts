/**
 * Tauri-backed {@link WindowControl} (phase-2 native shell). Wraps the shell's
 * `window_*` commands (apps/desktop/src-tauri/src/window.rs) and is injected
 * into the core window-control seam at boot when running under Tauri. In the
 * browser this never loads — the seam stays null and `window.fullscreen` is
 * ungranted. The dynamic import keeps `@tauri-apps/api` out of the browser
 * bundle (same pattern as tauriBackend.ts).
 */
import type { WindowControl } from '@horrible/core';

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd, args);
}

export function createTauriWindowControl(): WindowControl {
  return {
    isFullscreen: () => invoke<boolean>('window_is_fullscreen'),
    setFullscreen: (value) => invoke<boolean>('window_set_fullscreen', { value }),
    toggleFullscreen: () => invoke<boolean>('window_toggle_fullscreen'),
    minimize: () => invoke<void>('window_minimize'),
    isMaximized: () => invoke<boolean>('window_is_maximized'),
    toggleMaximize: () => invoke<boolean>('window_toggle_maximize'),
    close: () => invoke<void>('window_close'),
    startResizeDragging: (edge) =>
      invoke<void>('window_start_resize_dragging', { direction: edge }),
    openWorkspaceWindow: (workspaceId) => invoke<void>('window_open_workspace', { workspaceId }),
    openBrowserWindow: (url) => invoke<void>('browser_open_url', { url }),
    browserWebview: {
      create: (id, url, bounds) => invoke<void>('create_browser_webview', { id, url, bounds }),
      updateBounds: (id, bounds) => invoke<void>('update_browser_webview_bounds', { id, bounds }),
      setVisible: (id, visible) => invoke<void>('set_browser_webview_visible', { id, visible }),
      navigate: (id, url) => invoke<void>('navigate_browser_webview', { id, url }),
      close: (id) => invoke<void>('close_browser_webview', { id }),
    },
  };
}
