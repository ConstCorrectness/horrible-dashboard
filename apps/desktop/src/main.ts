/**
 * The desktop shell's main process. It stays thin: app logic lives in the backend
 * and packages/. Its jobs are supervising the backend (backend.ts), owning the one
 * frameless window the app draws its own chrome into, and answering the page's
 * commands — the same command names and argument shapes the Tauri shell answered,
 * so the frontend's seams (apps/web/src/desktop*.ts) did not have to learn a new
 * vocabulary.
 */
import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  net,
  protocol,
  shell,
  type IpcMainInvokeEvent,
} from 'electron';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';

import { BackendSupervisor } from './backend.js';
import { configureAppSession } from './sessions.js';
import { registerShortcuts, unregisterAllShortcuts } from './shortcuts.js';
import { checkForUpdate, installUpdate } from './updater.js';
import { isWebUrl, type Bounds } from './urls.js';
import { BrowserViews } from './webview.js';
import * as win from './window.js';

/** Where the packaged UI is served from. Must match backend/origins.py's UI_ORIGINS. */
const APP_SCHEME = 'app';
const APP_ORIGIN = 'app://horrible';
const DEV_URL = process.env.HORRIBLE_DESKTOP_DEV_URL ?? 'http://localhost:5173';

// --- Chromium switches: before `ready`, or they do nothing ------------------------

// Lobby voice and the mixer play without waiting for a click — the same policy the
// Tauri shell gave WebView2.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
if (process.platform === 'win32') {
  // Carried over from the Tauri shell's WebView2 arguments: on at least one Windows 11
  // machine the sandboxed audio service crash-looped, and while it is down
  // `enumerateDevices()` lists no audio devices at all, so every `getUserMedia`
  // fails with "Requested device not found". The audio service is the one that talks
  // to WASAPI; running it unsandboxed is what Chrome did for years and what the
  // enterprise `AudioSandboxEnabled=false` policy does — the renderer stays sandboxed.
  app.commandLine.appendSwitch('disable-features', 'AudioServiceSandbox');
}
if (process.platform === 'linux') {
  // Global shortcuts on Wayland go through the desktop portal; without the feature
  // `globalShortcut.register` silently does nothing there.
  app.commandLine.appendSwitch('enable-features', 'GlobalShortcutsPortal');
}

// --- Desktop identity: before the first window, or the shell can't match it -------

/** electron-builder.json's `appId`; the installer stamps it on its shortcuts. */
const APP_ID = 'com.horrible.dashboard';
/** The installed `.desktop` file's name, and `desktopName` in package.json. */
const LINUX_DESKTOP_ID = 'horrible-dashboard';

if (process.platform === 'win32') {
  // The taskbar groups windows by AppUserModelID. Unset, Electron's default
  // (`electron.app.<name>`) differs from the ID on the Start-menu shortcut, so a
  // pinned icon and the running window show as two buttons, and the running one
  // can't be pinned back onto the shortcut.
  app.setAppUserModelId(APP_ID);
}
if (process.platform === 'linux') {
  // Docks match a window to its `.desktop` entry by Wayland app_id (Electron takes
  // that from package.json's `desktopName`) or by X11 WM_CLASS — which Electron
  // takes from the app name, and ours is the package name, `@horrible/desktop`. So
  // the name is set to the entry's; but userData is derived from the name too, and
  // moving it would orphan every existing install's localStorage. Pin it first.
  const userData = app.getPath('userData');
  app.setName(LINUX_DESKTOP_ID);
  app.setPath('userData', userData);
}

// `app://` behaves like https: a real origin (localStorage, IndexedDB, CORS-checked
// fetches to the backend, ES modules, `crypto.subtle`), not `file://`'s opaque `null`
// one — which the backend's CORS and WebSocket origin checks rightly refuse.
protocol.registerSchemesAsPrivileged([
  {
    scheme: APP_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
      codeCache: true,
    },
  },
]);

const packaged = app.isPackaged;
const pageUrl = packaged ? `${APP_ORIGIN}/index.html` : DEV_URL;
const pageOrigin = new URL(pageUrl).origin;

function isAppOrigin(url: string): boolean {
  try {
    return new URL(url).origin === pageOrigin;
  } catch {
    return false;
  }
}

const supervisor = new BackendSupervisor(packaged ? process.resourcesPath : null);
let mainWindow: BrowserWindow | null = null;
let views: BrowserViews | null = null;

async function main(): Promise<void> {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  // The backend boots alongside the UI rather than after it.
  supervisor.start();

  await app.whenReady();
  // No app menu: it would bring Chromium's default accelerators (Ctrl+R, F11,
  // Ctrl+Shift+I, zoom) into a page whose keymap owns every one of them.
  Menu.setApplicationMenu(null);
  configureAppSession(isAppOrigin);
  if (packaged) serveUi();
  registerCommands();
  createWindow();
}

/** Serve `apps/web/dist` (shipped as `resources/web`) at `app://horrible/`. */
function serveUi(): void {
  const root = path.join(process.resourcesPath, 'web');
  protocol.handle(APP_SCHEME, async (request) => {
    const { host, pathname } = new URL(request.url);
    if (host !== 'horrible') return new Response('not found', { status: 404 });
    const relative = path.normalize(decodeURIComponent(pathname)).replace(/^([/\\])+/, '');
    const file = path.join(root, relative || 'index.html');
    // Never outside the UI directory, whatever `..` the URL carried.
    if (file !== root && !file.startsWith(root + path.sep)) {
      return new Response('forbidden', { status: 403 });
    }
    const response = await net.fetch(pathToFileURL(file).toString()).catch(() => null);
    if (response?.ok) return response;
    // A client-side route reloaded: the single-page app answers every path.
    return net.fetch(pathToFileURL(path.join(root, 'index.html')).toString());
  });
}

/**
 * The window's own icon. Packaged on Windows and macOS the executable's icon is the
 * window's; on Linux a binary carries none, so without one an X11 window shows a
 * generic icon in alt-tab and on any dock that doesn't match it to its `.desktop`.
 * electron-builder.json copies the PNG into resources for that.
 */
function windowIcon(): string | undefined {
  if (!packaged) return path.join(__dirname, '..', 'build', 'icon.png');
  if (process.platform === 'linux') return path.join(process.resourcesPath, 'icon.png');
  return undefined;
}

function createWindow(): void {
  const window = new BrowserWindow({
    title: 'horrible-dashboard',
    width: 1280,
    height: 800,
    minWidth: 640,
    minHeight: 480,
    // The app draws its own titlebar (TitleBar.tsx) under `chrome.workspaceTabs`.
    frame: false,
    // The page's own background, so neither the first frame nor a resize flashes white.
    backgroundColor: '#11111b',
    show: false,
    icon: windowIcon(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
      // The app keeps working in the background (voice rooms, agent runs, the
      // mixer); Chromium's timer throttling would starve them while unfocused.
      backgroundThrottling: false,
    },
  });
  mainWindow = window;
  views = new BrowserViews(window, (channel, payload) => {
    if (!window.isDestroyed()) window.webContents.send(channel, payload);
  });

  window.once('ready-to-show', () => window.show());
  window.on('closed', () => {
    mainWindow = null;
    views = null;
  });

  const wc = window.webContents;
  // The app page never becomes a browser: links that leave it open in the user's
  // browser, and `window.open` never creates a window that would inherit the preload.
  wc.setWindowOpenHandler(({ url }) => {
    if (isWebUrl(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  wc.on('will-navigate', (event, url) => {
    if (isAppOrigin(url)) return;
    event.preventDefault();
    if (isWebUrl(url)) void shell.openExternal(url);
  });
  wc.on('will-attach-webview', (event) => event.preventDefault());
  if (!packaged) {
    // DevTools in development (Tauri's debug builds had them too).
    wc.on('before-input-event', (_e, input) => {
      const devtools =
        input.type === 'keyDown' &&
        (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i'));
      if (devtools) wc.toggleDevTools();
    });
  }
  // A renderer crash otherwise leaves a dead, frameless window with no way out.
  // Reload — but not in a loop: a page that crashes again within ten seconds stays
  // down rather than pinning a core.
  let lastCrash = 0;
  wc.on('render-process-gone', (_e, details) => {
    if (details.reason === 'clean-exit') return;
    const now = Date.now();
    if (now - lastCrash > 10_000) wc.reload();
    lastCrash = now;
  });

  void loadPage(window);
}

/**
 * Load the UI. In development the Vite server may still be starting (dev-desktop.mjs
 * launches both at once), so keep retrying for a while instead of showing an error.
 */
async function loadPage(window: BrowserWindow): Promise<void> {
  const deadline = Date.now() + (packaged ? 0 : 60_000);
  for (;;) {
    try {
      await window.loadURL(pageUrl);
      return;
    } catch (err) {
      if (window.isDestroyed()) return;
      if (Date.now() > deadline) {
        const message = `Could not load the UI from ${pageUrl}: ${String(err)}`;
        console.error(`[desktop] ${message}`);
        void window.loadURL(`data:text/plain;charset=utf-8,${encodeURIComponent(message)}`);
        window.show();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
}

type Args = Record<string, unknown>;
type Command = (args: Args, window: BrowserWindow) => unknown;

const str = (args: Args, key: string): string => {
  const v = args[key];
  if (typeof v !== 'string') throw new Error(`\`${key}\` must be a string`);
  return v;
};
const bool = (args: Args, key: string): boolean => {
  const v = args[key];
  if (typeof v !== 'boolean') throw new Error(`\`${key}\` must be a boolean`);
  return v;
};
const bounds = (args: Args): Bounds => {
  const b = args.bounds as Partial<Bounds> | undefined;
  if (!b || typeof b !== 'object') throw new Error('`bounds` is required');
  return { x: Number(b.x), y: Number(b.y), width: Number(b.width), height: Number(b.height) };
};
const browserViews = (): BrowserViews => {
  if (!views) throw new Error('the window is gone');
  return views;
};

/** Every command the page may invoke. Anything else is refused by name. */
const COMMANDS: Record<string, Command> = {
  backend_status: () => supervisor.getStatus(),

  window_is_fullscreen: (_a, w) => w.isFullScreen(),
  window_set_fullscreen: (a, w) => win.setFullscreen(w, bool(a, 'value')),
  window_toggle_fullscreen: (_a, w) => win.toggleFullscreen(w),
  window_minimize: (_a, w) => w.minimize(),
  window_is_maximized: (_a, w) => w.isMaximized(),
  window_toggle_maximize: (_a, w) => win.toggleMaximize(w),
  window_close: (_a, w) => w.close(),
  window_set_zoom: (a, w) => win.setZoom(w, Number(a.factor)),
  window_start_resize_dragging: () => win.startResizeDragging(),
  browser_open_url: (a) => win.openBrowserWindow(str(a, 'url')),
  open_external: (a) => win.openExternal(str(a, 'url')),
  open_path: (a) => win.openPath(str(a, 'path')),

  create_browser_webview: (a) => browserViews().create(str(a, 'id'), str(a, 'url'), bounds(a)),
  update_browser_webview_bounds: (a) => browserViews().updateBounds(str(a, 'id'), bounds(a)),
  set_browser_webview_visible: (a) => browserViews().setVisible(str(a, 'id'), bool(a, 'visible')),
  navigate_browser_webview: (a) => browserViews().navigate(str(a, 'id'), str(a, 'url')),
  close_browser_webview: (a) => browserViews().close(str(a, 'id')),
  close_all_browser_webviews: () => browserViews().closeAll(),
  cdp_browser_webview: (a) =>
    browserViews().cdpCall(str(a, 'id'), str(a, 'method'), (a.params as Args | undefined) ?? {}),
  subscribe_browser_webview_cdp: (a) => browserViews().cdpSubscribe(str(a, 'id'), str(a, 'event')),
  open_browser_webview_devtools: (a) => browserViews().openDevtools(str(a, 'id')),

  shortcuts_register: (a, w) => {
    const accelerators = Array.isArray(a.accelerators) ? a.accelerators.map(String) : [];
    return registerShortcuts(accelerators, (accelerator) => {
      if (!w.isDestroyed()) w.webContents.send('global-shortcut', { accelerator });
    });
  },
  shortcuts_unregister_all: () => unregisterAllShortcuts(),

  updater_check: (a) => checkForUpdate(str(a, 'channel')),
  updater_install: (a) => installUpdate(str(a, 'channel'), () => supervisor.shutdown()),
};

function registerCommands(): void {
  ipcMain.handle(
    'desktop:invoke',
    async (event: IpcMainInvokeEvent, name: unknown, args: unknown) => {
      try {
        // Only the app's own page, in the main window, speaks to the shell. Browser
        // views have no preload, but a check here costs nothing and does not depend
        // on that staying true.
        const window = mainWindow;
        if (
          !window ||
          event.sender !== window.webContents ||
          !isAppOrigin(event.senderFrame?.url ?? '')
        ) {
          throw new Error('not allowed from this page');
        }
        const command = typeof name === 'string' ? COMMANDS[name] : undefined;
        if (!command) throw new Error(`unknown command: ${String(name)}`);
        const value = await command((args ?? {}) as Args, window);
        return { ok: true, value: value ?? null };
      } catch (err) {
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    },
  );
}

// --- Lifecycle ----------------------------------------------------------------

app.on('window-all-closed', () => app.quit());

let quitting = false;
app.on('will-quit', (event) => {
  unregisterAllShortcuts();
  if (quitting) return;
  // Hold the quit until the backend is down: an orphaned uvicorn keeps the port, and
  // on Windows keeps the install directory's interpreter locked.
  event.preventDefault();
  quitting = true;
  void supervisor.shutdown().finally(() => app.quit());
});

// One instance: a second launch (a desktop shortcut clicked twice, a protocol
// handler) focuses the running window instead of starting a second backend.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  void main();
}
