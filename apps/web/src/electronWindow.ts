/**
 * Electron-backed WindowControl (native desktop shell). Wraps the shell's
 * IPC methods exposed on window.horriblePlatform and is injected into the
 * core window-control seam at boot when running under Electron.
 */
import type {
  BrowserCdpEvent,
  BrowserWebviewEvent,
  ResizeEdge,
  WebviewBounds,
  WindowControl,
} from '@horrible/core';

interface HorriblePlatformWithWindow {
  window: {
    isFullscreen: () => Promise<boolean>;
    setFullscreen: (value: boolean) => Promise<boolean>;
    toggleFullscreen: () => Promise<boolean>;
    minimize: () => Promise<void>;
    isMaximized: () => Promise<boolean>;
    toggleMaximize: () => Promise<boolean>;
    close: () => Promise<void>;
    setZoom: (factor: number) => Promise<number>;
    startResizeDragging: (edge: ResizeEdge) => Promise<void>;
    openBrowserWindow: (url: string) => Promise<void>;
  };
  browserWebview: {
    create: (id: string, url: string, bounds: WebviewBounds) => Promise<void>;
    updateBounds: (id: string, bounds: WebviewBounds) => Promise<void>;
    setVisible: (id: string, visible: boolean) => Promise<void>;
    navigate: (id: string, url: string) => Promise<void>;
    close: (id: string) => Promise<void>;
    closeAll: () => Promise<void>;
    onEvent: (listener: (event: BrowserWebviewEvent) => void) => () => void;
    cdpCall: <T = Record<string, unknown>>(
      id: string,
      method: string,
      params?: Record<string, unknown>,
    ) => Promise<T>;
    cdpSubscribe: (id: string, event: string) => Promise<void>;
    cdpOnEvent: (listener: (event: BrowserCdpEvent) => void) => () => void;
    openDevtools: (id: string) => Promise<void>;
  };
}

let zoom = 1;
const scaled = (b: WebviewBounds): WebviewBounds =>
  zoom === 1 ? b : { x: b.x * zoom, y: b.y * zoom, width: b.width * zoom, height: b.height * zoom };

export function createElectronWindowControl(): WindowControl {
  const platform = (window as unknown as { horriblePlatform: HorriblePlatformWithWindow })
    .horriblePlatform;

  return {
    setZoom: async (factor) => {
      zoom = await platform.window.setZoom(factor);
      return zoom;
    },
    isFullscreen: () => platform.window.isFullscreen(),
    setFullscreen: (value) => platform.window.setFullscreen(value),
    toggleFullscreen: () => platform.window.toggleFullscreen(),
    minimize: () => platform.window.minimize(),
    isMaximized: () => platform.window.isMaximized(),
    toggleMaximize: () => platform.window.toggleMaximize(),
    close: () => platform.window.close(),
    startResizeDragging: (edge) => platform.window.startResizeDragging(edge),
    openBrowserWindow: (url) => platform.window.openBrowserWindow(url),
    browserWebview: {
      create: (id, url, bounds) => platform.browserWebview.create(id, url, scaled(bounds)),
      updateBounds: (id, bounds) => platform.browserWebview.updateBounds(id, scaled(bounds)),
      setVisible: (id, visible) => platform.browserWebview.setVisible(id, visible),
      navigate: (id, url) => platform.browserWebview.navigate(id, url),
      close: (id) => platform.browserWebview.close(id),
      closeAll: () => platform.browserWebview.closeAll(),
      onEvent: (listener) => platform.browserWebview.onEvent(listener),
      cdp: {
        call: <T>(id: string, method: string, params?: Record<string, unknown>) =>
          platform.browserWebview.cdpCall<T>(id, method, params),
        subscribe: (id, event) => platform.browserWebview.cdpSubscribe(id, event),
        onEvent: (listener) => platform.browserWebview.cdpOnEvent(listener),
        openDevtools: (id) => platform.browserWebview.openDevtools(id),
      },
    },
  };
}
