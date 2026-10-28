import { app, BrowserWindow, ipcMain } from 'electron';
import * as path from 'node:path';
import { BackendSupervisor } from './backend.js';
import { BrowserViewManager } from './browserView.js';
import { ShortcutsManager } from './shortcuts.js';
import type { ResizeEdge, WebviewBounds } from './types.js';
import { AppUpdater } from './updater.js';
import {
  closeWindow,
  isFullscreen,
  isMaximized,
  openBrowserWindow,
  openExternal,
  openPath,
  setFullscreen,
  setZoom,
  startResizeDragging,
  toggleFullscreen,
  toggleMaximize,
} from './window.js';

// Chromium GPU & rendering flags for maximum performance on Linux
app.commandLine.appendSwitch('enable-gpu-rasterization');
app.commandLine.appendSwitch('enable-zero-copy');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

const DEV_URL = process.env.ELECTRON_DEV_URL ?? 'http://localhost:5173';
const PLACEHOLDER_PATH = path.join(app.getAppPath(), 'placeholder.html');

let mainWindow: BrowserWindow | null = null;
let backendSupervisor: BackendSupervisor | null = null;
let browserViewManager: BrowserViewManager | null = null;
let shortcutsManager: ShortcutsManager | null = null;
const appUpdater = new AppUpdater();

function setupIpc(win: BrowserWindow): void {
  // Backend supervisor
  ipcMain.handle('backend_status', () => {
    return (
      backendSupervisor?.getStatus() ?? {
        state: 'unavailable',
        origin: null,
        error: 'Backend supervisor not initialized',
      }
    );
  });

  // Window controls
  ipcMain.handle('window_is_fullscreen', () => isFullscreen(win));
  ipcMain.handle('window_set_fullscreen', (_e, value: boolean) => setFullscreen(win, value));
  ipcMain.handle('window_toggle_fullscreen', () => toggleFullscreen(win));
  ipcMain.handle('window_minimize', () => win.minimize());
  ipcMain.handle('window_is_maximized', () => isMaximized(win));
  ipcMain.handle('window_toggle_maximize', () => toggleMaximize(win));
  ipcMain.handle('window_close', () => closeWindow(win));
  ipcMain.handle('window_set_zoom', (_e, factor: number) => setZoom(win, factor));
  ipcMain.handle('window_start_resize_dragging', (_e, edge: ResizeEdge) =>
    startResizeDragging(win, edge),
  );
  ipcMain.handle('browser_open_url', (_e, url: string) => openBrowserWindow(url));

  // External open
  ipcMain.handle('open_external', (_e, url: string) => openExternal(url));
  ipcMain.handle('open_path', (_e, targetPath: string) => openPath(targetPath));

  // Child webviews & CDP
  browserViewManager = new BrowserViewManager(win);
  ipcMain.handle('create_browser_webview', (_e, id: string, url: string, bounds: WebviewBounds) =>
    browserViewManager?.create(id, url, bounds),
  );
  ipcMain.handle('update_browser_webview_bounds', (_e, id: string, bounds: WebviewBounds) =>
    browserViewManager?.updateBounds(id, bounds),
  );
  ipcMain.handle('set_browser_webview_visible', (_e, id: string, visible: boolean) =>
    browserViewManager?.setVisible(id, visible),
  );
  ipcMain.handle('navigate_browser_webview', (_e, id: string, url: string) =>
    browserViewManager?.navigate(id, url),
  );
  ipcMain.handle('close_browser_webview', (_e, id: string) => browserViewManager?.close(id));
  ipcMain.handle('close_all_browser_webviews', () => browserViewManager?.closeAll());
  ipcMain.handle('cdp_call', (_e, id: string, method: string, params?: Record<string, unknown>) =>
    browserViewManager?.cdpCall(id, method, params),
  );
  ipcMain.handle('cdp_subscribe', (_e, id: string, event: string) =>
    browserViewManager?.subscribeCdp(id, event),
  );
  ipcMain.handle('open_browser_webview_devtools', (_e, id: string) =>
    browserViewManager?.openDevtools(id),
  );

  // Global shortcuts
  shortcutsManager = new ShortcutsManager(win);
  ipcMain.handle(
    'shortcuts_register',
    (_e, accelerators: string[]) => shortcutsManager?.register(accelerators) ?? [],
  );
  ipcMain.handle('shortcuts_unregister_all', () => shortcutsManager?.unregisterAll());

  // In-app auto-updater
  ipcMain.handle('updater_check', (_e, { channel }: { channel: string }) =>
    appUpdater.checkForUpdate(channel),
  );
  ipcMain.handle('updater_install', (_e, { channel }: { channel: string }) =>
    appUpdater.installUpdate(channel),
  );
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 640,
    minHeight: 480,
    title: 'horrible-dashboard',
    frame: false, // frameless for custom titlebar
    backgroundColor: '#11111b',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow = win;
  setupIpc(win);

  win.webContents.on('did-fail-load', (_event, errorCode, _desc, validatedURL) => {
    const isAborted = errorCode === -3;
    if (!isAborted && validatedURL.startsWith('http')) {
      void win.loadFile(PLACEHOLDER_PATH, { query: { devUrl: DEV_URL } });
    }
  });

  if (app.isPackaged) {
    const prodPath = path.join(process.resourcesPath, 'web', 'index.html');
    void win.loadFile(prodPath);
  } else {
    win.loadURL(DEV_URL).catch(() => undefined);
  }
}

void app.whenReady().then(() => {
  backendSupervisor = new BackendSupervisor(app.getAppPath());
  void backendSupervisor.start();

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('will-quit', () => {
  shortcutsManager?.unregisterAll();
  backendSupervisor?.shutdown();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
