/**
 * Host seam for **native OS-window control** (the phase-2 native shell). The
 * desktop entry (apps/web running under Tauri) injects a Tauri-backed
 * implementation that drives the shell's `window_*` commands
 * (apps/desktop/src-tauri/src/window.rs); the browser leaves the seam null.
 *
 * Feature code always gates on the matching capability before reaching for the
 * control (`window.fullscreen` for {@link WindowControl.toggleFullscreen}), so a
 * null control never surfaces to the user. Keeping the Tauri wiring in the app
 * entry and only a plain interface here preserves the "packages/ are
 * platform-agnostic; the entry owns Tauri" boundary. See
 * docs/architecture/layout-shell.mdx.
 */

/** A window edge/corner an OS resize-drag can start from. */
export type ResizeEdge =
  | 'east'
  | 'west'
  | 'north'
  | 'south'
  | 'north-east'
  | 'north-west'
  | 'south-east'
  | 'south-west';

/** Pane rectangle in logical (CSS) pixels — what `getBoundingClientRect()` reports. */
export interface WebviewBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * `browser.nativeWebview` — a real child webview overlaid on a browser pane.
 *
 * Grouped rather than flattened onto {@link WindowControl}: these five drive one
 * feature and are meaningless individually. Gate on the `browser.nativeWebview`
 * capability before reaching for them; the property is absent in the browser build.
 *
 * The overlay is composited by the OS **above** the HTML layer, so it cannot be
 * z-indexed under anything the app draws. {@link setVisible} is how callers yield
 * that region — for the command palette, modals, pane drags, or a workspace switch.
 * See apps/desktop/src-tauri/src/webview.rs.
 */
export interface BrowserWebviewControl {
  /** Create (or re-point and show) the overlay owned by pane `id`. Idempotent. */
  create(id: string, url: string, bounds: WebviewBounds): Promise<void>;
  /** Follow the pane as it moves or resizes. */
  updateBounds(id: string, bounds: WebviewBounds): Promise<void>;
  /** Show/hide without destroying — preserves the page, its scroll and its JS state. */
  setVisible(id: string, visible: boolean): Promise<void>;
  /** Point the overlay at a new URL (URL bar, bookmark, history, home). */
  navigate(id: string, url: string): Promise<void>;
  /** Destroy the overlay. */
  close(id: string): Promise<void>;
}

export interface WindowControl {
  /** Is the OS window currently borderless-fullscreen? */
  isFullscreen(): Promise<boolean>;
  /** Set OS-window fullscreen; resolves to the resulting state. */
  setFullscreen(value: boolean): Promise<boolean>;
  /** Flip OS-window fullscreen; resolves to the resulting state. */
  toggleFullscreen(): Promise<boolean>;

  // chrome.workspaceTabs — the custom titlebar drives the window itself.
  /** Minimize the window (titlebar minimize button). */
  minimize(): Promise<void>;
  /** Is the window currently maximized? (drives the restore icon) */
  isMaximized(): Promise<boolean>;
  /** Toggle maximize/restore; resolves to the new maximized state. */
  toggleMaximize(): Promise<boolean>;
  /** Close the window (titlebar close button). */
  close(): Promise<void>;
  /** Begin an OS resize-drag from a window edge/corner. */
  startResizeDragging(edge: ResizeEdge): Promise<void>;

  // window.perWorkspace — open a workspace in its own OS window.
  /** Open (or focus) an OS window showing the given workspace. */
  openWorkspaceWindow(workspaceId: string): Promise<void>;

  // browser.nativeWindow — the embedded browser pops a page out to a real
  // native window (a true browser, so sites that refuse iframing still open).
  /** Open `url` in a new decorated native browser window. */
  openBrowserWindow(url: string): Promise<void>;

  // browser.nativeWebview — a native child webview overlaid on a browser pane.
  /** Present only on hosts granting `browser.nativeWebview`. */
  browserWebview?: BrowserWebviewControl;
}

let control: WindowControl | null = null;

/** Wire the native window control (desktop entry); pass null to clear. */
export function setWindowControl(impl: WindowControl | null): void {
  control = impl;
}

/** The native window control, or null in the browser (no native shell). */
export function windowControl(): WindowControl | null {
  return control;
}
