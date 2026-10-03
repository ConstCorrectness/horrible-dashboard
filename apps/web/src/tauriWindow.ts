/**
 * Tauri-backed {@link WindowControl} (phase-2 native shell). Wraps the shell's
 * `window_*` commands (apps/desktop/src-tauri/src/window.rs) and is injected
 * into the core window-control seam at boot when running under Tauri. In the
 * browser this never loads — the seam stays null and `window.fullscreen` is
 * ungranted. The dynamic import keeps `@tauri-apps/api` out of the browser
 * bundle (same pattern as tauriBackend.ts).
 */
import type {
  BrowserCdpEvent,
  BrowserWebviewEvent,
  WebviewBounds,
  WindowControl,
} from '@horrible/core';

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd, args);
}

/**
 * Fan one Tauri event out to many listeners through a single subscription, made
 * lazily on the first listener. Every browser pane and tab listens to the same
 * stream and filters by id; one `listen` per pane would multiply the IPC traffic
 * by the number of panes for no reason.
 */
function eventHub<T>(name: string): (listener: (payload: T) => void) => () => void {
  const listeners = new Set<(payload: T) => void>();
  let started = false;
  return (listener) => {
    listeners.add(listener);
    if (!started) {
      started = true;
      void import('@tauri-apps/api/event').then(({ listen }) =>
        listen<T>(name, (event) => listeners.forEach((fn) => fn(event.payload))),
      );
    }
    return () => {
      listeners.delete(listener);
    };
  };
}

const onWebviewEvent = eventHub<BrowserWebviewEvent>('browser-webview');
const onCdpEvent = eventHub<BrowserCdpEvent>('browser-cdp');

/**
 * The main webview's page zoom. A native browser pane is a *separate* child webview
 * positioned in window pixels, while the pane measures itself in CSS pixels — which
 * a zoomed page makes bigger. Its bounds are scaled by the zoom on the way out.
 */
let zoom = 1;
const scaled = (b: WebviewBounds): WebviewBounds =>
  zoom === 1 ? b : { x: b.x * zoom, y: b.y * zoom, width: b.width * zoom, height: b.height * zoom };

export function createTauriWindowControl(opts: { cdp: boolean }): WindowControl {
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
    openWorkspaceWindow: (workspaceId) => invoke<void>('window_open_workspace', { workspaceId }),
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
      cdp: opts.cdp
        ? {
            call: <T>(id: string, method: string, params?: Record<string, unknown>) =>
              invoke<T>('cdp_browser_webview', { id, method, params: params ?? {} }),
            subscribe: (id, event) => invoke<void>('subscribe_browser_webview_cdp', { id, event }),
            onEvent: onCdpEvent,
            openDevtools: (id) => invoke<void>('open_browser_webview_devtools', { id }),
          }
        : undefined,
    },
  };
}
