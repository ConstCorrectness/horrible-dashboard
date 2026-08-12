/**
 * Remote updates, from the frontend's side.
 *
 * Both commands live in the desktop shell (apps/desktop/src-tauri/src/updater.rs)
 * because only it can replace the running executable. In the **browser layout
 * there is nothing to update** — the page is served fresh on every load — so this
 * surface reports "not applicable" rather than an error, which is a different
 * thing again from "checked and up to date".
 */
import { isDesktopShell } from '../../external';

export interface UpdateInfo {
  available: boolean;
  currentVersion: string;
  version: string | null;
  notes: string | null;
  date: string | null;
  /** Echoed back so a beta build is never displayed under a "stable" label. */
  channel: string;
  /**
   * Set when the check could not complete (offline, no signing key configured).
   * Distinct from `available: false`, which means we asked and there is nothing.
   */
  error: string | null;
}

interface TauriInternals {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
}

function invoker(): TauriInternals['invoke'] | null {
  const internals = (window as { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__;
  return internals?.invoke?.bind(internals) ?? null;
}

/** True when this layout can install an update at all. */
export function updatesSupported(): boolean {
  return isDesktopShell();
}

/** Rust returns snake_case; the rest of the app speaks camelCase. */
interface RawUpdateInfo {
  available: boolean;
  current_version: string;
  version: string | null;
  notes: string | null;
  date: string | null;
  channel: string;
  error: string | null;
}

export async function checkForUpdate(channel: string): Promise<UpdateInfo | null> {
  const invoke = invoker();
  if (!invoke) return null;
  const raw = (await invoke('updater_check', { channel })) as RawUpdateInfo;
  return {
    available: raw.available,
    currentVersion: raw.current_version,
    version: raw.version,
    notes: raw.notes,
    date: raw.date,
    channel: raw.channel,
    error: raw.error,
  };
}

/**
 * Install and restart. Resolves `false` when the manifest no longer offers
 * anything; on success the process is replaced and nothing resolves at all —
 * which is why callers must not put cleanup after the await.
 */
export async function installUpdate(channel: string): Promise<boolean> {
  const invoke = invoker();
  if (!invoke) return false;
  return (await invoke('updater_install', { channel })) as boolean;
}
