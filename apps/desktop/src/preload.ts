/**
 * The app page's one door into the shell: `window.horribleDesktop`.
 *
 * Deliberately tiny and generic — `invoke(command, args)` and `listen(event, fn)` —
 * so the frontend keeps one command vocabulary (the same names and argument shapes
 * the Tauri shell had) and this file never grows a method per feature. What each
 * command may do is decided in main.ts, which also checks that a call comes from the
 * app's own page. Runs sandboxed: only `contextBridge` and `ipcRenderer` exist here.
 */
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

/** Shell → page events the page may listen to. */
const EVENTS = new Set(['global-shortcut', 'browser-webview', 'browser-cdp']);

type Reply = { ok: true; value: unknown } | { ok: false; error: string };

contextBridge.exposeInMainWorld('horribleDesktop', {
  shell: 'electron',
  platform: process.platform === 'win32' ? 'win' : process.platform === 'darwin' ? 'mac' : 'linux',
  /**
   * Run a shell command. Rejects with the shell's error *message* (a string), as
   * Tauri's `invoke` did, so callers that show it show a sentence and not
   * "Error invoking remote method".
   */
  invoke: async (command: string, args?: Record<string, unknown>): Promise<unknown> => {
    const reply = (await ipcRenderer.invoke('desktop:invoke', command, args ?? {})) as Reply;
    if (reply.ok) return reply.value;
    throw reply.error;
  },
  /** Listen to a shell event; returns an unsubscribe. */
  listen: (event: string, listener: (payload: unknown) => void): (() => void) => {
    if (!EVENTS.has(event)) throw new Error(`unknown desktop event: ${event}`);
    const handler = (_e: IpcRendererEvent, payload: unknown) => listener(payload);
    ipcRenderer.on(event, handler);
    return () => {
      ipcRenderer.removeListener(event, handler);
    };
  },
});
