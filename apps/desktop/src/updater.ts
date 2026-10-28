/**
 * Remote updates: check the channel's manifest, download, **verify**, install.
 *
 * **The channel is a runtime setting.** The same binary must be able to follow stable
 * or beta, so the feed is set per call: a permanent, non-moving release tagged
 * `updater` holds one manifest per channel and platform (`latest.yml`,
 * `latest-linux.yml`, `beta.yml`, …), each pointing at assets on the *real* release.
 * Deliberately not `/releases/latest/download/`: GitHub resolves "latest" to the
 * newest non-prerelease, so a beta manifest would be unreachable there.
 *
 * **The signature is the whole security model.** electron-updater checks the
 * download's sha512 against the manifest, but manifest and installer come from the
 * same place, so that only proves the bytes arrived intact. Every installer is also
 * signed in CI with the release key (minisign — the same key the Tauri shell used, so
 * the identity carries over), and nothing is installed unless that signature
 * verifies against the public key compiled in below. There is deliberately no
 * "skip verification" path, and `autoInstallOnAppQuit` is off so an unverified
 * download can never be installed behind our back on quit.
 *
 * **What an update must not touch.** Everything under `$HORRIBLE_DATA_DIR` is
 * versioned independently of the app (llama.cpp builds, GGUFs, traces). The installer
 * is never pointed at that directory — a property of where the data lives, which is
 * exactly why it is written down here.
 */
import { app, net } from 'electron';
import { autoUpdater, type UpdateInfo as FeedUpdateInfo } from 'electron-updater';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';

import { parsePublicKey, verifySignature } from './minisign.js';
import { UPDATER_PUBKEY } from './updater-key.js';

const MANIFEST_BASE =
  'https://github.com/ConstCorrectness/horrible-dashboard/releases/download/updater';

/** What `updater_check` answers — the same shape the Tauri shell returned. */
export interface UpdateInfo {
  /** True when the feed answered *and* offered something newer. */
  available: boolean;
  current_version: string;
  version: string | null;
  notes: string | null;
  date: string | null;
  /** Which channel was actually checked, so a UI can never show beta under "stable". */
  channel: string;
  /** Set when the check could not complete — distinct from `available: false`. */
  error: string | null;
}

function normalize(channel: string): 'stable' | 'beta' {
  return channel === 'beta' ? 'beta' : 'stable';
}

let configured = false;

function useChannel(channel: 'stable' | 'beta'): void {
  if (!configured) {
    configured = true;
    autoUpdater.autoDownload = false;
    // Never install on quit: only `install` does, and only after the signature check.
    autoUpdater.autoInstallOnAppQuit = false;
    autoUpdater.allowDowngrade = false;
    autoUpdater.logger = null;
  }
  // electron-updater's own channel names: `latest` is stable.
  const feedChannel = channel === 'beta' ? 'beta' : 'latest';
  autoUpdater.setFeedURL({ provider: 'generic', url: MANIFEST_BASE, channel: feedChannel });
  autoUpdater.channel = feedChannel;
  autoUpdater.allowPrerelease = channel === 'beta';
}

function notesOf(info: FeedUpdateInfo): string | null {
  const notes = info.releaseNotes;
  if (typeof notes === 'string') return notes;
  if (Array.isArray(notes)) return notes.map((n) => n.note ?? '').join('\n') || null;
  return null;
}

/**
 * Ask the channel's manifest whether anything newer exists.
 *
 * Never throws for a network problem: an offline laptop is not an error the user
 * needs a dialog about, and a caller that cannot tell "no update" from "could not
 * ask" will report the wrong one.
 */
export async function checkForUpdate(rawChannel: string): Promise<UpdateInfo> {
  const channel = normalize(rawChannel);
  const base: UpdateInfo = {
    available: false,
    current_version: app.getVersion(),
    version: null,
    notes: null,
    date: null,
    channel,
    error: null,
  };
  if (!app.isPackaged) {
    return { ...base, error: 'a development build does not update itself' };
  }
  try {
    useChannel(channel);
    const result = await autoUpdater.checkForUpdates();
    if (!result) return { ...base, error: 'updates are not supported for this install' };
    if (!result.isUpdateAvailable) return base;
    const info = result.updateInfo;
    return {
      ...base,
      available: true,
      version: info.version,
      notes: notesOf(info),
      date: info.releaseDate || null,
    };
  } catch (err) {
    return { ...base, error: err instanceof Error ? err.message : String(err) };
  }
}

/** The manifest entry the updater downloaded to `file`. */
function entryFor(info: FeedUpdateInfo, file: string): { url: string } | null {
  const name = path.basename(file);
  return (
    info.files.find(
      (f) =>
        decodeURIComponent(path.basename(new URL(f.url, MANIFEST_BASE + '/').pathname)) === name,
    ) ??
    info.files[0] ??
    null
  );
}

/**
 * Download the update the same check would find, verify its signature, install it
 * and restart.
 *
 * Re-checks rather than trusting an earlier `updater_check`: the two calls are
 * separated by however long the user spent reading the release notes, and a
 * re-published release may have replaced the asset since.
 *
 * `beforeInstall` stops the backend first: on Windows its interpreter lives inside
 * the install directory, and the installer cannot replace files a running process
 * holds open.
 */
export async function installUpdate(
  rawChannel: string,
  beforeInstall: () => Promise<void>,
): Promise<boolean> {
  if (!app.isPackaged) throw new Error('a development build does not update itself');
  useChannel(normalize(rawChannel));
  const result = await autoUpdater.checkForUpdates();
  if (!result?.isUpdateAvailable) return false;

  const [file] = await autoUpdater.downloadUpdate();
  if (!file) throw new Error('the update downloaded no file');
  const entry = entryFor(result.updateInfo, file);
  if (!entry) throw new Error('the update manifest lists no files');
  const sigUrl = `${new URL(entry.url, MANIFEST_BASE + '/').toString()}.sig`;

  try {
    const response = await net.fetch(sigUrl);
    if (!response.ok)
      throw new Error(`no signature for the update (${response.status} from ${sigUrl})`);
    verifySignature(parsePublicKey(UPDATER_PUBKEY), await response.text(), await fs.readFile(file));
  } catch (err) {
    // Do not leave an unverified installer where a later run could pick it up.
    await fs.rm(file, { force: true }).catch(() => undefined);
    throw new Error(`refusing to install: ${err instanceof Error ? err.message : String(err)}`);
  }

  await beforeInstall();
  // Silent (the Tauri shell's "passive" mode had no questions either), then relaunch.
  autoUpdater.quitAndInstall(true, true);
  return true;
}
