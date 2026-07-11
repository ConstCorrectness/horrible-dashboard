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
