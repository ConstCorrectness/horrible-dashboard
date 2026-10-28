/**
 * `browser.nativeWebview` and `browser.nativeCdp` — a **real Chromium view** overlaid
 * on the embedded-browser pane, and the agent's hands inside it.
 *
 * The browser module's other two modes both compromise: the `<iframe>` is refused by
 * any site sending `X-Frame-Options`/CSP `frame-ancestors`, and the server-rendered
 * Chromium engine streams JPEG frames. On the desktop there is a third option: a
 * `WebContentsView` parked over the pane's placeholder.
 *
 * ## The catch
 *
 * A view is **not** part of the HTML layer. It is a sibling surface composited
 * *above* everything the frontend draws — the command palette, dropdowns, dialogs,
 * drag previews. There is no z-index that reaches it, which is why
 * `set_browser_webview_visible` exists alongside the positioning commands: the
 * frontend hides the view whenever something must render on top of it
 * (NativeBrowserView.tsx).
 *
 * ## CDP
 *
 * In-process, through `webContents.debugger` — scoped to exactly one view, and
 * unreachable from outside this process. A `--remote-debugging-port` would instead
 * hand **every** webContents, the app's own page and the tokens it holds included, to
 * any program on the machine. Methods and events are allowlisted (cdpPolicy.ts).
 */
import { type BrowserWindow, type DownloadItem, WebContentsView } from 'electron';

import { checkParams, eventAllowed, methodAllowed } from './cdpPolicy.js';
import { WEB_PREFERENCES_BROWSER, browserSession } from './sessions.js';
import { type Bounds, isWebUrl, parseWebUrl, sanitizeBounds, wantsPopupWindow } from './urls.js';

/**
 * How long a CDP call may take before the command gives up. `Page.navigate` returns
 * once the navigation *commits*, not when the page loads, so this is generous.
 */
const CDP_CALL_TIMEOUT_MS = 30_000;

/** What a native page does on its own, sent to the app page as `browser-webview`. */
export type WebviewEvent =
  | { kind: 'load'; id: string; url: string; loading: boolean }
  | { kind: 'title'; id: string; title: string }
  | { kind: 'newTab'; id: string; url: string }
  | {
      kind: 'download';
      id: string;
      url: string;
      path: string | null;
      state: 'started' | 'done' | 'failed';
    };

export interface CdpEvent {
  id: string;
  method: string;
  params: unknown;
}

interface Pane {
  view: WebContentsView;
  /** CDP events forwarded for this view (`Page.loadEventFired`, …). */
  cdpEvents: Set<string>;
}

export class BrowserViews {
  private panes = new Map<string, Pane>();
  /** webContents id → pane id, for downloads (reported per session, not per view). */
  private owners = new Map<number, string>();
  private downloadsHooked = false;

  constructor(
    private readonly window: BrowserWindow,
    private readonly emit: (channel: 'browser-webview' | 'browser-cdp', payload: unknown) => void,
  ) {}

  private get(id: string): Pane {
    const pane = this.panes.get(id);
    if (!pane) throw new Error(`no native browser webview with id \`${id}\``);
    return pane;
  }

  /**
   * Create (or re-point and show) the view for pane `id` at `bounds`.
   *
   * Idempotent: a pane that remounts — a workspace switch, a React StrictMode double
   * mount — must not spawn a second surface. An existing view is repositioned and
   * navigated instead.
   */
  create(id: string, url: string, bounds: Bounds): void {
    const target = parseWebUrl(url).toString();
    const existing = this.panes.get(id);
    if (existing) {
      existing.view.setBounds(sanitizeBounds(bounds));
      existing.view.setVisible(true);
      this.load(existing.view, target);
      return;
    }

    const view = new WebContentsView({
      webPreferences: { ...WEB_PREFERENCES_BROWSER, session: browserSession() },
    });
    view.setBackgroundColor('#ffffff');
    view.setBounds(sanitizeBounds(bounds));
    this.window.contentView.addChildView(view);
    this.panes.set(id, { view, cdpEvents: new Set() });
    this.owners.set(view.webContents.id, id);
    this.hook(id, view);
    this.hookDownloads();
    this.load(view, target);
  }

  /**
   * Start a navigation and return without waiting for it. The Tauri shell resolved
   * `navigate` once the navigation was *started*, and the pane relies on that: it
   * follows progress through `load` events, and a slow site must not hold up the
   * command (and with it the pane's next bounds update). A failed load is the page's
   * business — Chromium's error page and a `load` event — not a command failure.
   */
  private load(view: WebContentsView, url: string): void {
    view.webContents.loadURL(url).catch(() => undefined);
  }

  /** What makes the view a *browser*: each hook keeps the pane's chrome honest. */
  private hook(id: string, view: WebContentsView): void {
    const wc = view.webContents;
    const load = (loading: boolean, url = wc.getURL()) =>
      this.emit('browser-webview', { kind: 'load', id, url, loading } satisfies WebviewEvent);

    wc.on('did-start-navigation', (details) => {
      if (details.isMainFrame && !details.isSameDocument) load(true, details.url);
    });
    wc.on('did-finish-load', () => load(false));
    wc.on('did-fail-load', (_e, code, _desc, url, isMainFrame) => {
      // -3 is ERR_ABORTED: a navigation replaced by another one, not a failure.
      if (isMainFrame && code !== -3) load(false, url);
    });
    // SPA route changes: no load, but the URL bar must follow.
    wc.on('did-navigate-in-page', (_e, url, isMainFrame) => {
      if (isMainFrame) load(false, url);
    });
    wc.on('page-title-updated', (_e, title) =>
      this.emit('browser-webview', { kind: 'title', id, title } satisfies WebviewEvent),
    );
    wc.setWindowOpenHandler(({ url, features }) => {
      // OAuth and payment popups keep their `window.opener`: let them be windows.
      if (wantsPopupWindow(features)) {
        return {
          action: 'allow',
          overrideBrowserWindowOptions: {
            autoHideMenuBar: true,
            webPreferences: { ...WEB_PREFERENCES_BROWSER, session: browserSession() },
          },
        };
      }
      if (isWebUrl(url)) {
        this.emit('browser-webview', { kind: 'newTab', id, url } satisfies WebviewEvent);
      }
      return { action: 'deny' };
    });
    // The debugger detaches by itself when the page crashes or DevTools takes it
    // over; forget the forwarded events so a later subscribe re-arms them.
    wc.debugger.on('detach', () => this.panes.get(id)?.cdpEvents.clear());
    wc.debugger.on('message', (_e, method, params) => {
      if (this.panes.get(id)?.cdpEvents.has(method)) {
        this.emit('browser-cdp', { id, method, params } satisfies CdpEvent);
      }
    });
    wc.on('render-process-gone', () => load(false));
  }

  /**
   * Downloads land in the user's Downloads folder (Chromium's default) with no save
   * dialog — the same behaviour WebView2 had — and the pane only reports them.
   */
  private hookDownloads(): void {
    if (this.downloadsHooked) return;
    this.downloadsHooked = true;
    browserSession().on('will-download', (_event, item: DownloadItem, wc) => {
      const id = wc ? this.owners.get(wc.id) : undefined;
      if (id === undefined) return;
      const url = item.getURL();
      const report = (state: 'started' | 'done' | 'failed') =>
        this.emit('browser-webview', {
          kind: 'download',
          id,
          url,
          path: item.getSavePath() || null,
          state,
        } satisfies WebviewEvent);
      report('started');
      item.once('done', (_e, state) => report(state === 'completed' ? 'done' : 'failed'));
    });
  }

  /** Follow the pane: every resize, split drag, scroll and dock change. */
  updateBounds(id: string, bounds: Bounds): void {
    this.get(id).view.setBounds(sanitizeBounds(bounds));
  }

  /**
   * Show or hide without destroying — the occlusion control. Hiding preserves the
   * page, its scroll position and its JS state, which closing would not.
   */
  setVisible(id: string, visible: boolean): void {
    this.get(id).view.setVisible(visible);
  }

  navigate(id: string, url: string): void {
    this.load(this.get(id).view, parseWebUrl(url).toString());
  }

  /** Destroy the view for pane `id`. Forgets it even if the close itself fails. */
  close(id: string): void {
    const pane = this.panes.get(id);
    if (!pane) return;
    this.panes.delete(id);
    this.owners.delete(pane.view.webContents.id);
    try {
      if (!this.window.isDestroyed()) this.window.contentView.removeChildView(pane.view);
    } catch {
      // The window is going away; the view goes with it.
    }
    // A removed view is not destroyed by itself; its renderer process would live on.
    if (!pane.view.webContents.isDestroyed()) pane.view.webContents.close();
  }

  /**
   * Destroy every view. The frontend calls this once at boot: a reload wipes every
   * pane session that owned a view while the OS window — and its views — live on,
   * leaving a page composited over the app that nothing could hide or close again.
   */
  closeAll(): void {
    for (const id of [...this.panes.keys()]) this.close(id);
  }

  // --- browser.nativeCdp ------------------------------------------------------

  private attach(pane: Pane): Electron.Debugger {
    const dbg = pane.view.webContents.debugger;
    if (!dbg.isAttached()) dbg.attach('1.3');
    return dbg;
  }

  /** Run one allowlisted CDP method on view `id`; resolves with its result object. */
  async cdpCall(id: string, method: string, params?: Record<string, unknown>): Promise<unknown> {
    if (!methodAllowed(method)) throw new Error(`CDP method \`${method}\` is not allowed`);
    const args = params ?? {};
    checkParams(method, args);
    const dbg = this.attach(this.get(id));
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`CDP \`${method}\` timed out`)),
        CDP_CALL_TIMEOUT_MS,
      );
    });
    try {
      return await Promise.race([dbg.sendCommand(method, args), timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Forward CDP event `event` from view `id` as `browser-cdp`. Idempotent per pair —
   * a StrictMode remount subscribes twice by construction. Only the *receiver* is
   * armed here; the domain still has to be enabled (`Page.enable`) through
   * {@link cdpCall} before Chromium emits anything.
   */
  cdpSubscribe(id: string, event: string): void {
    if (!eventAllowed(event)) throw new Error(`CDP event \`${event}\` is not allowed`);
    const pane = this.get(id);
    this.attach(pane);
    pane.cdpEvents.add(event);
  }

  /** Open DevTools for view `id` (F12 in the pane), detached. */
  openDevtools(id: string): void {
    this.get(id).view.webContents.openDevTools({ mode: 'detach' });
  }
}
