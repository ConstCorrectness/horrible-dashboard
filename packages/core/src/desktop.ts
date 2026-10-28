/**
 * The desktop shell's bridge, as the page sees it: `window.horribleDesktop`, exposed
 * by the Electron shell's preload (apps/desktop/src/preload.ts). Absent in the
 * browser layout.
 *
 * Two verbs and nothing else — `invoke(command, args)` runs one of the shell's
 * commands (apps/desktop/src/main.ts lists them), `listen(event, fn)` subscribes to
 * one of its events — so the shell never grows a method per feature, and every seam
 * that talks to it (window control, global shortcuts, external links, updates) does
 * so in the same words. `invoke` rejects with the shell's error message as a string.
 */

export interface DesktopBridge {
  shell: 'electron';
  platform: 'win' | 'mac' | 'linux';
  invoke(command: string, args?: Record<string, unknown>): Promise<unknown>;
  listen(event: string, listener: (payload: unknown) => void): () => void;
}

/** The shell bridge, or null in the browser layout. */
export function desktopBridge(): DesktopBridge | null {
  return (window as { horribleDesktop?: DesktopBridge }).horribleDesktop ?? null;
}

/** True when running inside the desktop shell (vs the browser layout). */
export function isDesktopShell(): boolean {
  return desktopBridge() !== null;
}

/** Run a shell command. Throws outside the desktop shell. */
export async function desktopInvoke<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const bridge = desktopBridge();
  if (!bridge) throw new Error(`\`${command}\` needs the desktop shell`);
  return (await bridge.invoke(command, args)) as T;
}
