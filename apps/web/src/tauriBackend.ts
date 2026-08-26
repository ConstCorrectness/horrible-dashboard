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
 * How long to keep polling a shell that is still saying `starting`.
 *
 * **Not a timeout on the backend** — the shell already owns that, and owning it twice
 * is the bug this constant used to be. It was 15s, which was comfortable while the only
 * backend was a checkout's warm venv and is not once a packaged app boots a
 * [bundled runtime](../../../docs/architecture/releases.mdx): a first launch pays for
 * Windows Defender reading ~50,000 freshly-written files before uvicorn imports
 * anything, and the backend's own startup brings up the peer fabric and publishes
 * presence. Measured cold, that is comfortably past 15s.
 *
 * Giving up first does not *look* like a failure, which is what makes it expensive: the
 * app boots successfully, pluginless, showing a backend-down hint about a backend that
 * came up ten seconds later. So this is now a backstop against a supervisor wedged in
 * `starting` forever, set beyond the shell's own worst case (three ready-timeouts plus
 * backoff, after which it reports `failed` and this loop exits on the state, not the
 * clock).
 */
const WEDGED_SUPERVISOR_TIMEOUT_MS = 180_000;

/**
 * Poll the shell until the backend is ready (→ origin) or terminally not
 * coming (→ null; boot proceeds pluginless and the home view shows the
 * backend-down hint). Capped so a wedged supervisor can't block boot forever.
 */
export async function resolveBackendOrigin(): Promise<string | null> {
  // Dynamic import keeps @tauri-apps/api out of the browser bundle.
  const { invoke } = await import('@tauri-apps/api/core');
  const deadline = Date.now() + WEDGED_SUPERVISOR_TIMEOUT_MS;
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
