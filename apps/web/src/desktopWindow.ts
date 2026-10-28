/**
 * Desktop-shell {@link WindowControl}. Wraps the shell's `window_*` and browser-view
 * commands (apps/desktop/src/window.ts, webview.ts) and is injected into the core
 * window-control seam at boot when running under the shell. In the browser this is
 * never called — the seam stays null and `window.fullscreen` is ungranted.
 */
import {
  desktopBridge,
  desktopInvoke as invoke,
  type BrowserCdpEvent,
  type BrowserWebviewEvent,
  type WebviewBounds,
  type WindowControl,
} from '@horrible/core';

/**
 * Fan one shell event out to many listeners through a single subscription, made
 * lazily on the first listener. Every browser pane and tab listens to the same
 * stream and filters by id; one subscription per pane would multiply the IPC
 * traffic by the number of panes for no reason.
 */
function eventHub<T>(name: string): (listener: (payload: T) => void) => () => void {
  const listeners = new Set<(payload: T) => void>();
  let started = false;
  return (listener) => {
    listeners.add(listener);
    if (!started) {
      started = true;
      desktopBridge()?.listen(name, (payload) => listeners.forEach((fn) => fn(payload as T)));
    }
    return () => {
      listeners.delete(listener);
    };
  };
}

const onWebviewEvent = eventHub<BrowserWebviewEvent>('browser-webview');
const onCdpEvent = eventHub<BrowserCdpEvent>('browser-cdp');

/**
 * The main page's zoom. A native browser pane is a *separate* view positioned in
 * window pixels, while the pane measures itself in CSS pixels — which
 * a zoomed page makes bigger. Its bounds are scaled by the zoom on the way out.
 */
let zoom = 1;
const scaled = (b: WebviewBounds): WebviewBounds =>
  zoom === 1 ? b : { x: b.x * zoom, y: b.y * zoom, width: b.width * zoom, height: b.height * zoom };

export function createDesktopWindowControl(): WindowControl {
  return {
    setZoom: async (factor) => {
      zoom = await invoke<number>('window_set_zoom', { factor });
      return zoom;
    },
    isFullscreen: () => invoke<boolean>('window_is_fullscreen'),
    setFullscreen: (value) => invoke<boolean>('window_set_fullscreen', { value }),
    toggleFullscreen: () => invoke<boolean>('window_toggle_fullscreen'),
    minimize: () => invoke<void>('window_minimize'),
    isMaximized: () => invoke<boolean>('window_is_maximized'),
    toggleMaximize: () => invoke<boolean>('window_toggle_maximize'),
    close: () => invoke<void>('window_close'),
    startResizeDragging: (edge) =>
      invoke<void>('window_start_resize_dragging', { direction: edge }),
    openBrowserWindow: (url) => invoke<void>('browser_open_url', { url }),
    browserWebview: {
      create: (id, url, bounds) =>
        invoke<void>('create_browser_webview', { id, url, bounds: scaled(bounds) }),
      updateBounds: (id, bounds) =>
        invoke<void>('update_browser_webview_bounds', { id, bounds: scaled(bounds) }),
      setVisible: (id, visible) => invoke<void>('set_browser_webview_visible', { id, visible }),
      navigate: (id, url) => invoke<void>('navigate_browser_webview', { id, url }),
      close: (id) => invoke<void>('close_browser_webview', { id }),
      closeAll: () => invoke<void>('close_all_browser_webviews'),
      onEvent: onWebviewEvent,
      // Chromium on every platform, so the CDP bridge is too.
      cdp: {
        call: <T>(id: string, method: string, params?: Record<string, unknown>) =>
          invoke<T>('cdp_browser_webview', { id, method, params: params ?? {} }),
        subscribe: (id, event) => invoke<void>('subscribe_browser_webview_cdp', { id, event }),
        onEvent: onCdpEvent,
        openDevtools: (id) => invoke<void>('open_browser_webview_devtools', { id }),
      },
    },
  };
}
