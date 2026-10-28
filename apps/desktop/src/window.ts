/**
 * Native OS-window controls exposed to the frontend.
 *
 * Two capabilities ride these commands:
 * - `window.fullscreen` — borderless OS-window fullscreen, distinct from the
 *   desktop's in-window presentation (a pane filling the page).
 * - `chrome.workspaceTabs` — the app draws the window's own titlebar (the window is
 *   frameless), so the frontend drives minimize/maximize/close itself. Move-drag and
 *   double-click-maximize use the page's `-webkit-app-region: drag` strip
 *   (TitleBar.tsx), not a command here.
 */
import { BrowserWindow, shell } from 'electron';
import * as fs from 'node:fs';

import { WEB_PREFERENCES_BROWSER, browserSession } from './sessions.js';
import { parseWebUrl } from './urls.js';

/** Windows that were maximized when they went fullscreen, so leaving puts them back. */
const maximizedBeforeFullscreen = new WeakSet<BrowserWindow>();

/**
 * Enter OS-fullscreen, **un-maximizing first**.
 *
 * On Windows an undecorated maximized window has its client area clipped to the
 * monitor's *work area* so the taskbar stays usable, and that clip can survive the
 * switch to fullscreen: the frame grows to the whole monitor while the page stays
 * work-area tall — a taskbar-shaped black band along the bottom. Dropping the
 * maximized state first leaves nothing to clip; it is restored on the way out.
 */
function enterFullscreen(win: BrowserWindow): void {
  if (win.isMaximized()) {
    win.unmaximize();
    maximizedBeforeFullscreen.add(win);
  }
  win.setFullScreen(true);
}

function leaveFullscreen(win: BrowserWindow): void {
  win.setFullScreen(false);
  if (maximizedBeforeFullscreen.delete(win)) win.maximize();
}

export function setFullscreen(win: BrowserWindow, value: boolean): boolean {
  if (value) enterFullscreen(win);
  else leaveFullscreen(win);
  return value;
}

export function toggleFullscreen(win: BrowserWindow): boolean {
  return setFullscreen(win, !win.isFullScreen());
}

/**
 * Toggle maximize/restore; returns the new maximized state.
 *
 * **While fullscreen this leaves fullscreen instead of maximizing** — maximizing a
 * fullscreen frameless window is the second way into the black band
 * `enterFullscreen` exists to prevent, and refusing outright would leave a dead
 * button on a titlebar that is still drawn in fullscreen.
 */
export function toggleMaximize(win: BrowserWindow): boolean {
  if (win.isFullScreen()) {
    leaveFullscreen(win);
    return win.isMaximized();
  }
  if (win.isMaximized()) {
    win.unmaximize();
    return false;
  }
  win.maximize();
  return true;
}

/**
 * Page zoom for the main window's own page — the desktop's Ctrl+= / Ctrl+- / Ctrl+0
 * (the app menu, and with it Chromium's own zoom accelerators, is removed). Clamped
 * to the range Chromium offers; returns the factor applied. Browser-pane views keep
 * their own zoom; the frontend scales their bounds by this factor
 * (apps/web/src/desktopWindow.ts).
 */
export function setZoom(win: BrowserWindow, factor: number): number {
  if (!Number.isFinite(factor)) throw new Error('zoom factor must be a number');
  const clamped = Math.min(5, Math.max(0.25, factor));
  win.webContents.setZoomFactor(clamped);
  return clamped;
}

/**
 * Begin an OS resize-drag from a window edge — a no-op here, on purpose.
 *
 * Electron's frameless windows keep native resize borders (a few pixels just inside
 * each edge, hit-tested by the OS before the page sees the pointer), so the
 * frontend's invisible edge grips never receive the press in the first place. The
 * command exists so the frontend's `WindowControl` contract stays the same.
 */
export function startResizeDragging(): void {}

/**
 * Open `url` in the user's **default system browser** — the external-link path
 * (OAuth consent pages, docs links). OAuth must run in the real browser: existing
 * sessions and password managers work there, and Google rejects embedded browsers
 * outright (RFC 8252). Only `http`/`https`.
 */
export async function openExternal(url: string): Promise<void> {
  await shell.openExternal(parseWebUrl(url).toString());
}

/**
 * Show a **directory** in the OS file manager (the Storage settings' roots).
 *
 * **It must be a directory, and that is the security boundary, not a nicety.**
 * `shell.openPath` opens a path the way a double-click would, which for an
 * executable, a `.desktop` file or a script means *running* it. The check is on the
 * resolved path, so `…/data/../../something.exe` and a symlink cannot smuggle a file
 * past a textual test.
 */
export async function openPath(target: string): Promise<void> {
  let resolved: string;
  try {
    resolved = fs.realpathSync(target);
  } catch (err) {
    throw new Error(`${target}: ${(err as Error).message}`);
  }
  if (!fs.statSync(resolved).isDirectory()) {
    throw new Error(`refusing to open a non-directory path: ${target}`);
  }
  const error = await shell.openPath(resolved);
  if (error) throw new Error(error);
}

/**
 * Open `url` in a new **decorated** native browser window — the embedded browser's
 * escape hatch for sites that refuse iframing (`X-Frame-Options`/CSP). A real window
 * bypasses those headers entirely. It runs in the browser-pane session (no preload,
 * no app bridge, sandboxed), never the app's own.
 */
export async function openBrowserWindow(url: string): Promise<void> {
  const parsed = parseWebUrl(url);
  const win = new BrowserWindow({
    width: 1024,
    height: 768,
    title: parsed.toString(),
    autoHideMenuBar: true,
    webPreferences: { ...WEB_PREFERENCES_BROWSER, session: browserSession() },
  });
  await win.loadURL(parsed.toString());
}
