import { BrowserWindow, shell } from 'electron';
import * as fs from 'node:fs';
import type { ResizeEdge } from './types.js';

let wasMaximizedBeforeFullscreen = false;

export function isFullscreen(win: BrowserWindow): boolean {
  return win.isFullScreen();
}

export function setFullscreen(win: BrowserWindow, value: boolean): boolean {
  if (value) {
    wasMaximizedBeforeFullscreen = win.isMaximized();
    if (wasMaximizedBeforeFullscreen) {
      win.unmaximize();
    }
    win.setFullScreen(true);
  } else {
    win.setFullScreen(false);
    if (wasMaximizedBeforeFullscreen) {
      win.maximize();
    }
  }
  return win.isFullScreen();
}

export function toggleFullscreen(win: BrowserWindow): boolean {
  return setFullscreen(win, !win.isFullScreen());
}

export function minimize(win: BrowserWindow): void {
  win.minimize();
}

export function isMaximized(win: BrowserWindow): boolean {
  return win.isMaximized();
}

export function toggleMaximize(win: BrowserWindow): boolean {
  if (win.isFullScreen()) {
    setFullscreen(win, false);
    return win.isMaximized();
  }
  if (win.isMaximized()) {
    win.unmaximize();
    return false;
  } else {
    win.maximize();
    return true;
  }
}

export function closeWindow(win: BrowserWindow): void {
  win.close();
}

export function setZoom(win: BrowserWindow, factor: number): number {
  if (!Number.isFinite(factor)) return 1.0;
  const clamped = Math.max(0.25, Math.min(5.0, factor));
  win.webContents.setZoomFactor(clamped);
  return clamped;
}

export async function openExternal(url: string): Promise<boolean> {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      console.warn(`[window] Refusing to open non-http(s) external URL: ${url}`);
      return false;
    }
    await shell.openExternal(url);
    return true;
  } catch (err) {
    console.error(`[window] Failed to open external URL ${url}:`, err);
    return false;
  }
}

export async function openPath(targetPath: string): Promise<boolean> {
  try {
    const resolved = fs.realpathSync(targetPath);
    const stat = fs.statSync(resolved);
    if (!stat.isDirectory()) {
      console.warn(`[window] Refusing to open non-directory path: ${targetPath}`);
      return false;
    }
    const result = await shell.openPath(resolved);
    // shell.openPath returns an empty string on success, or an error message on failure
    return result === '';
  } catch (err) {
    console.error(`[window] Failed to open path ${targetPath}:`, err);
    return false;
  }
}

let browserWindowSeq = 0;

export async function openBrowserWindow(url: string): Promise<void> {
  const parsed = new URL(url);
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`Refusing to open non-http(s) URL in browser window: ${url}`);
  }

  browserWindowSeq++;
  const win = new BrowserWindow({
    width: 1024,
    height: 768,
    title: url,
    frame: true, // decorated window for browser pop-out
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  await win.loadURL(url);
}

export function startResizeDragging(_win: BrowserWindow, _edge: ResizeEdge): void {
  // Electron handles window resizing natively when resizable: true is enabled
  // and CSS border handles or native window borders are used.
}
