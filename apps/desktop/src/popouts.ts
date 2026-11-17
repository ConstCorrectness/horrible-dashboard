/**
 * Pop-out windows: a workbench tab group living in its own OS window.
 *
 * ## One realm, not a second app
 *
 * A pop-out is **not** a second load of the UI. The app page opens
 * `window.open('', 'horrible-popout:<id>')` and renders the group into that blank
 * document itself (React `createPortal`), so every store, the one socket to the
 * backend and the one `AudioContext` stay in the main page. This file only lets
 * those windows exist and looks after them as OS windows: where they may open,
 * their zoom, and telling the page when one is moved or closed.
 *
 * ## What the spike measured (Electron 44, 2026-11-16)
 *
 * - A child window does **not** inherit the opener's zoom: it is synced here,
 *   after its `about:blank` has loaded (see `adopt`).
 * - Each child's webContents throttles on its own, so a pop-out behind the main
 *   window stops painting unless it gets `backgroundThrottling: false` too.
 * - Children **outlive a reload** of the main page. Their content belonged to the
 *   old page's realm, so they are closed whenever the main page navigates or dies.
 *
 * A pop-out cannot drive the shell. Its `about:blank` document gets no
 * `horribleDesktop` bridge (measured: the preload does not run there), and were
 * one ever exposed, `desktop:invoke` answers only the main window's webContents
 * (main.ts).
 */
import { type BrowserWindow, type BrowserWindowConstructorOptions, screen, shell } from 'electron';

import { clampToWorkArea } from './popoutBounds.js';
import { isWebUrl, type Bounds } from './urls.js';

/** How long a move or resize must settle before it is reported. */
const REPORT_DEBOUNCE_MS = 250;

/** Shell → page, on the `popout` channel. */
export type PopoutEvent =
  | {
      kind: 'bounds';
      id: string;
      bounds: { x: number; y: number; w: number; h: number };
      maximized: boolean;
      displayId: number;
    }
  | { kind: 'closed'; id: string };

/** Options for a pop-out's BrowserWindow. Bounds come from the page's features. */
export function popoutWindowOptions(): BrowserWindowConstructorOptions {
  return {
    // A native frame: the OS draws the title (the page sets `document.title`) and
    // the minimize/maximize/close buttons. Close means "dock these panes back".
    frame: true,
    autoHideMenuBar: true,
    backgroundColor: '#11111b',
    minWidth: 320,
    minHeight: 200,
    show: true,
    webPreferences: { backgroundThrottling: false },
  };
}

export class PopoutWindows {
  private windows = new Map<string, BrowserWindow>();
  /** Windows closed by the shell itself, whose `closed` the page must not see. */
  private silenced = new WeakSet<BrowserWindow>();

  constructor(
    private readonly main: BrowserWindow,
    private readonly emit: (payload: PopoutEvent) => void,
  ) {}

  /** The pop-out window for `id`, if it is open. */
  get(id: string): BrowserWindow | null {
    const w = this.windows.get(id);
    return w && !w.isDestroyed() ? w : null;
  }

  /** Take charge of a window the page just opened as pop-out `id`. */
  adopt(id: string, win: BrowserWindow): void {
    const previous = this.get(id);
    if (previous && previous !== win) previous.close();
    this.windows.set(id, win);

    win.setBounds(clampToWorkArea(win.getBounds(), workAreas()));

    const wc = win.webContents;
    // Twice, and the second one is what holds. `did-create-window` runs before the
    // child's `about:blank` commits, and the commit replaces whatever zoom was set
    // with Chromium's saved per-host zoom for `about:blank` — persisted in the
    // profile, so a pop-out came up at 1.0 (or at whatever a past session left)
    // beside a main window at 1.1. Measured 2026-11-17: a zoom set at
    // `did-finish-load` sticks. The first call covers a window adopted after its
    // load already finished.
    const syncZoom = () => {
      if (!wc.isDestroyed() && !this.main.isDestroyed()) {
        wc.setZoomFactor(this.main.webContents.getZoomFactor());
      }
    };
    syncZoom();
    wc.once('did-finish-load', syncZoom);

    // The pop-out's document is the page's to fill; it never navigates or opens
    // windows of its own. Links clicked in it leave for the user's browser, as
    // they do from the main window.
    wc.setWindowOpenHandler(({ url }) => {
      if (isWebUrl(url)) void shell.openExternal(url);
      return { action: 'deny' };
    });
    wc.on('will-navigate', (event, url) => {
      event.preventDefault();
      if (isWebUrl(url)) void shell.openExternal(url);
    });
    wc.on('will-attach-webview', (event) => event.preventDefault());

    let timer: NodeJS.Timeout | null = null;
    const report = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        if (win.isDestroyed()) return;
        // The restored size while maximized, so docking back and reopening does
        // not come back as a maximized-sized normal window.
        const b = win.isMaximized() ? win.getNormalBounds() : win.getBounds();
        this.emit({
          kind: 'bounds',
          id,
          bounds: { x: b.x, y: b.y, w: b.width, h: b.height },
          maximized: win.isMaximized(),
          displayId: screen.getDisplayMatching(b).id,
        });
      }, REPORT_DEBOUNCE_MS);
    };
    win.on('moved', report);
    win.on('resized', report);
    win.on('maximize', report);
    win.on('unmaximize', report);
    report();

    win.once('closed', () => {
      if (timer) clearTimeout(timer);
      if (this.windows.get(id) === win) this.windows.delete(id);
      if (!this.silenced.has(win)) this.emit({ kind: 'closed', id });
    });
  }

  /** Follow the main window's zoom (window.ts `setZoom`). */
  setZoom(factor: number): void {
    for (const w of this.windows.values()) {
      if (!w.isDestroyed()) w.webContents.setZoomFactor(factor);
    }
  }

  /**
   * Close every pop-out: the page that filled them is gone. Silently — a
   * `closed` event would reach the *reloaded* page, which would dock back the
   * very pop-outs its saved layout is about to reopen.
   */
  closeAll(): void {
    for (const w of [...this.windows.values()]) {
      if (w.isDestroyed()) continue;
      this.silenced.add(w);
      w.close();
    }
    this.windows.clear();
  }
}

function workAreas(): Bounds[] {
  return screen.getAllDisplays().map((d) => d.workArea);
}
