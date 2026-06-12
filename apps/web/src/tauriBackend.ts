/**
 * Tauri-only backend origin handshake. The desktop shell spawns and supervises
 * uvicorn (see apps/desktop/src-tauri/src/backend.rs) and reports its state via
 * the `backend_status` command; boot polls it and hands the origin to
 * `initBackendOrigin`. In the browser none of this runs — paths stay relative
 * and the Vite proxy applies.
 */

interface BackendStatus {
  state: 'starting' | 'ready' | 'failed' | 'unavailable';
  origin: string | null;
  error: string | null;
}

export function isTauri(): boolean {
  return '__TAURI_INTERNALS__' in window;
}

/**
 * Poll the shell until the backend is ready (→ origin) or terminally not
 * coming (→ null; boot proceeds pluginless and the home view shows the
 * backend-down hint). Capped so a wedged supervisor can't block boot forever.
 */
export async function resolveBackendOrigin(): Promise<string | null> {
  // Dynamic import keeps @tauri-apps/api out of the browser bundle.
  const { invoke } = await import('@tauri-apps/api/core');
  const deadline = Date.now() + 15_000;
  for (;;) {
    try {
      const status = await invoke<BackendStatus>('backend_status');
      if (status.state === 'ready') return status.origin;
      if (status.state === 'failed' || status.state === 'unavailable') {
        if (status.error) console.error('[backend]', status.state, status.error);
        return null;
      }
    } catch (err) {
      console.error('[backend] backend_status invoke failed', err);
      return null;
    }
    if (Date.now() > deadline) return null;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
