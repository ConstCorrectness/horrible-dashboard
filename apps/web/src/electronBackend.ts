/**
 * Electron backend origin handshake. The Electron desktop shell supervises
 * the FastAPI backend (see apps/desktop-electron/src/backend.ts) and reports
 * its state via window.horriblePlatform.backend; boot polls it and hands the
 * origin to initBackendOrigin. In the browser none of this runs.
 */

interface BackendStatus {
  state: 'starting' | 'ready' | 'failed' | 'unavailable';
  origin: string | null;
  error: string | null;
}

interface HorriblePlatformWithBackend {
  shell: string;
  backend: {
    getStatus: () => Promise<BackendStatus>;
  };
}

export function isElectron(): boolean {
  return (
    'horriblePlatform' in window &&
    (window as unknown as { horriblePlatform: { shell?: string } }).horriblePlatform?.shell ===
      'electron'
  );
}

const WEDGED_SUPERVISOR_TIMEOUT_MS = 180_000;

/**
 * Poll the Electron shell until the backend is ready (-> origin) or terminally not
 * coming (-> null). Capped so a wedged supervisor cannot block boot forever.
 */
export async function resolveElectronBackendOrigin(): Promise<string | null> {
  const platform = (window as unknown as { horriblePlatform?: HorriblePlatformWithBackend })
    .horriblePlatform;
  if (!platform?.backend) return null;

  const deadline = Date.now() + WEDGED_SUPERVISOR_TIMEOUT_MS;
  for (;;) {
    try {
      const status = await platform.backend.getStatus();
      if (status.state === 'ready') return status.origin;
      if (status.state === 'failed' || status.state === 'unavailable') {
        if (status.error) console.error('[backend]', status.state, status.error);
        return null;
      }
    } catch (err) {
      console.error('[backend] Electron getStatus failed', err);
      return null;
    }
    if (Date.now() > deadline) return null;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
