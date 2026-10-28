import { contextBridge, ipcRenderer } from 'electron';
import type {
  BackendStatus,
  BrowserCdpEvent,
  BrowserWebviewEvent,
  ResizeEdge,
  WebviewBounds,
} from './types.js';

const DESKTOP_CAPABILITIES = [
  'fs.nativeDialogs',
  'shell.revealInOS',
  'notifications.system',
  'window.multi',
  'window.fullscreen',
  'chrome.workspaceTabs',
  'shortcuts.global',
  'tray',
  'browser.nativeWindow',
  'browser.nativeWebview',
  'media.displayCapture',
  'browser.nativeCdp',
] as const;

const platform =
  process.platform === 'win32' ? 'win' : process.platform === 'darwin' ? 'mac' : 'linux';

const horriblePlatform = {
  shell: 'electron',
  platform,
  capabilities: DESKTOP_CAPABILITIES,

  backend: {
    getStatus: (): Promise<BackendStatus> => ipcRenderer.invoke('backend_status'),
  },

  window: {
    isFullscreen: (): Promise<boolean> => ipcRenderer.invoke('window_is_fullscreen'),
    setFullscreen: (value: boolean): Promise<boolean> =>
      ipcRenderer.invoke('window_set_fullscreen', value),
    toggleFullscreen: (): Promise<boolean> => ipcRenderer.invoke('window_toggle_fullscreen'),
    minimize: (): Promise<void> => ipcRenderer.invoke('window_minimize'),
    isMaximized: (): Promise<boolean> => ipcRenderer.invoke('window_is_maximized'),
    toggleMaximize: (): Promise<boolean> => ipcRenderer.invoke('window_toggle_maximize'),
    close: (): Promise<void> => ipcRenderer.invoke('window_close'),
    setZoom: (factor: number): Promise<number> => ipcRenderer.invoke('window_set_zoom', factor),
    startResizeDragging: (edge: ResizeEdge): Promise<void> =>
      ipcRenderer.invoke('window_start_resize_dragging', edge),
    openBrowserWindow: (url: string): Promise<void> => ipcRenderer.invoke('browser_open_url', url),
  },

  browserWebview: {
    create: (id: string, url: string, bounds: WebviewBounds): Promise<void> =>
      ipcRenderer.invoke('create_browser_webview', id, url, bounds),
    updateBounds: (id: string, bounds: WebviewBounds): Promise<void> =>
      ipcRenderer.invoke('update_browser_webview_bounds', id, bounds),
    setVisible: (id: string, visible: boolean): Promise<void> =>
      ipcRenderer.invoke('set_browser_webview_visible', id, visible),
    navigate: (id: string, url: string): Promise<void> =>
      ipcRenderer.invoke('navigate_browser_webview', id, url),
    close: (id: string): Promise<void> => ipcRenderer.invoke('close_browser_webview', id),
    closeAll: (): Promise<void> => ipcRenderer.invoke('close_all_browser_webviews'),
    onEvent: (listener: (event: BrowserWebviewEvent) => void): (() => void) => {
      const handler = (_e: Electron.IpcRendererEvent, event: BrowserWebviewEvent) =>
        listener(event);
      ipcRenderer.on('browser-webview', handler);
      return () => {
        ipcRenderer.removeListener('browser-webview', handler);
      };
    },
    cdpCall: <T = Record<string, unknown>>(
      id: string,
      method: string,
      params?: Record<string, unknown>,
    ): Promise<T> => ipcRenderer.invoke('cdp_call', id, method, params),
    cdpSubscribe: (id: string, event: string): Promise<void> =>
      ipcRenderer.invoke('cdp_subscribe', id, event),
    cdpOnEvent: (listener: (event: BrowserCdpEvent) => void): (() => void) => {
      const handler = (_e: Electron.IpcRendererEvent, event: BrowserCdpEvent) => listener(event);
      ipcRenderer.on('browser-cdp', handler);
      return () => {
        ipcRenderer.removeListener('browser-cdp', handler);
      };
    },
    openDevtools: (id: string): Promise<void> =>
      ipcRenderer.invoke('open_browser_webview_devtools', id),
  },

  shortcuts: {
    register: (accelerators: string[]): Promise<string[]> =>
      ipcRenderer.invoke('shortcuts_register', accelerators),
    unregisterAll: (): Promise<void> => ipcRenderer.invoke('shortcuts_unregister_all'),
    onTrigger: (listener: (accelerator: string) => void): (() => void) => {
      const handler = (_e: Electron.IpcRendererEvent, data: { accelerator: string }) =>
        listener(data.accelerator);
      ipcRenderer.on('global-shortcut', handler);
      return () => {
        ipcRenderer.removeListener('global-shortcut', handler);
      };
    },
  },

  openExternal: (url: string): Promise<boolean> => ipcRenderer.invoke('open_external', url),
  openPath: (path: string): Promise<boolean> => ipcRenderer.invoke('open_path', path),

  updater: {
    check: (channel: string) => ipcRenderer.invoke('updater_check', { channel }),
    install: (channel: string) => ipcRenderer.invoke('updater_install', { channel }),
  },
};

contextBridge.exposeInMainWorld('horriblePlatform', horriblePlatform);
