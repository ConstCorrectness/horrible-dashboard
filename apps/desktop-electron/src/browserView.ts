import { WebContentsView, type BrowserWindow } from 'electron';
import type { BrowserCdpEvent, BrowserWebviewEvent, WebviewBounds } from './types.js';

function methodAllowed(method: string): boolean {
  const DOMAINS = ['Page', 'Runtime', 'DOM', 'Input', 'Accessibility'];
  const METHODS = ['Network.enable', 'Network.disable', 'Network.getResponseBody'];
  const [domain] = method.split('.');
  if (!domain) return false;
  return DOMAINS.includes(domain) || METHODS.includes(method);
}

export class BrowserViewManager {
  private views = new Map<string, WebContentsView>();
  private cdpSubscriptions = new Set<string>(); // "id:event"

  constructor(private mainWindow: BrowserWindow) {}

  private emitWebviewEvent(event: BrowserWebviewEvent): void {
    if (!this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send('browser-webview', event);
    }
  }

  private emitCdpEvent(event: BrowserCdpEvent): void {
    if (!this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send('browser-cdp', event);
    }
  }

  public async create(id: string, url: string, bounds: WebviewBounds): Promise<void> {
    const existing = this.views.get(id);
    if (existing) {
      existing.setBounds(bounds);
      existing.setVisible(true);
      if (url && existing.webContents.getURL() !== url) {
        await existing.webContents.loadURL(url);
      }
      return;
    }

    const view = new WebContentsView({
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    this.mainWindow.contentView.addChildView(view);
    view.setBounds(bounds);

    view.webContents.on('did-start-navigation', (_e, navUrl) => {
      this.emitWebviewEvent({
        kind: 'load',
        id,
        url: navUrl,
        loading: true,
      });
    });

    view.webContents.on('did-finish-load', () => {
      this.emitWebviewEvent({
        kind: 'load',
        id,
        url: view.webContents.getURL(),
        loading: false,
      });
    });

    view.webContents.on('page-title-updated', (_e, title) => {
      this.emitWebviewEvent({
        kind: 'title',
        id,
        title,
      });
    });

    view.webContents.setWindowOpenHandler((details) => {
      this.emitWebviewEvent({
        kind: 'newTab',
        id,
        url: details.url,
      });
      return { action: 'deny' };
    });

    view.webContents.session.on('will-download', (_event, item) => {
      const itemUrl = item.getURL();
      this.emitWebviewEvent({
        kind: 'download',
        id,
        url: itemUrl,
        path: item.getSavePath() || null,
        state: 'started',
      });

      item.once('done', (_e, state) => {
        this.emitWebviewEvent({
          kind: 'download',
          id,
          url: itemUrl,
          path: item.getSavePath() || null,
          state: state === 'completed' ? 'done' : 'failed',
        });
      });
    });

    this.views.set(id, view);

    if (url) {
      await view.webContents.loadURL(url);
    }
  }

  public updateBounds(id: string, bounds: WebviewBounds): void {
    const view = this.views.get(id);
    if (view) {
      view.setBounds(bounds);
    }
  }

  public setVisible(id: string, visible: boolean): void {
    const view = this.views.get(id);
    if (view) {
      view.setVisible(visible);
    }
  }

  public async navigate(id: string, url: string): Promise<void> {
    const view = this.views.get(id);
    if (view) {
      await view.webContents.loadURL(url);
    }
  }

  public close(id: string): void {
    const view = this.views.get(id);
    if (view) {
      if (view.webContents.debugger.isAttached()) {
        try {
          view.webContents.debugger.detach();
        } catch {
          // ignore
        }
      }
      this.mainWindow.contentView.removeChildView(view);
      this.views.delete(id);
      // Clean up CDP subscriptions for this webview
      const prefix = `${id}:`;
      for (const sub of this.cdpSubscriptions) {
        if (sub.startsWith(prefix)) {
          this.cdpSubscriptions.delete(sub);
        }
      }
    }
  }

  public closeAll(): void {
    for (const id of Array.from(this.views.keys())) {
      this.close(id);
    }
    this.cdpSubscriptions.clear();
  }

  public async cdpCall<T = Record<string, unknown>>(
    id: string,
    method: string,
    params?: Record<string, unknown>,
  ): Promise<T> {
    if (!methodAllowed(method)) {
      throw new Error(`CDP method not allowed: ${method}`);
    }

    const view = this.views.get(id);
    if (!view) {
      throw new Error(`Webview ${id} not found`);
    }

    const dbg = view.webContents.debugger;
    if (!dbg.isAttached()) {
      dbg.attach('1.3');
      dbg.on('message', (_event, cdpMethod, cdpParams) => {
        if (this.cdpSubscriptions.has(`${id}:${cdpMethod}`)) {
          this.emitCdpEvent({
            id,
            method: cdpMethod,
            params: cdpParams as Record<string, unknown>,
          });
        }
      });
    }

    return (await dbg.sendCommand(method, params ?? {})) as T;
  }

  public subscribeCdp(id: string, event: string): void {
    this.cdpSubscriptions.add(`${id}:${event}`);
    const view = this.views.get(id);
    if (view && !view.webContents.debugger.isAttached()) {
      view.webContents.debugger.attach('1.3');
      view.webContents.debugger.on('message', (_e, cdpMethod, cdpParams) => {
        if (this.cdpSubscriptions.has(`${id}:${cdpMethod}`)) {
          this.emitCdpEvent({
            id,
            method: cdpMethod,
            params: cdpParams as Record<string, unknown>,
          });
        }
      });
    }
  }

  public async openDevtools(id: string): Promise<void> {
    const view = this.views.get(id);
    if (view) {
      view.webContents.openDevTools({ mode: 'detach' });
    }
  }
}
