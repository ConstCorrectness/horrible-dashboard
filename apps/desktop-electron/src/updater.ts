import { app } from 'electron';
import { autoUpdater, type UpdateInfo as ElectronUpdateInfo } from 'electron-updater';

export interface RawUpdateInfo {
  available: boolean;
  current_version: string;
  version: string | null;
  notes: string | null;
  date: string | null;
  channel: string;
  error: string | null;
}

export class AppUpdater {
  private updateDownloaded = false;

  constructor() {
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;

    autoUpdater.on('update-downloaded', () => {
      this.updateDownloaded = true;
    });
  }

  public async checkForUpdate(rawChannel: string): Promise<RawUpdateInfo> {
    const channel = rawChannel === 'beta' ? 'beta' : 'stable';
    const currentVersion = app.getVersion();

    if (!app.isPackaged) {
      return {
        available: false,
        current_version: currentVersion,
        version: null,
        notes: null,
        date: null,
        channel,
        error: null,
      };
    }

    try {
      autoUpdater.channel = channel;
      const result = await autoUpdater.checkForUpdates();
      if (!result || !result.updateInfo) {
        return {
          available: false,
          current_version: currentVersion,
          version: null,
          notes: null,
          date: null,
          channel,
          error: null,
        };
      }

      const info: ElectronUpdateInfo = result.updateInfo;
      const isNewer = info.version !== currentVersion;

      let notes: string | null = null;
      if (typeof info.releaseNotes === 'string') {
        notes = info.releaseNotes;
      } else if (Array.isArray(info.releaseNotes)) {
        notes = info.releaseNotes.map((n) => (typeof n === 'string' ? n : n.note)).join('\n');
      }

      return {
        available: isNewer,
        current_version: currentVersion,
        version: isNewer ? info.version : null,
        notes,
        date: info.releaseDate || null,
        channel,
        error: null,
      };
    } catch (err) {
      console.warn('[updater] Check for update failed:', err);
      return {
        available: false,
        current_version: currentVersion,
        version: null,
        notes: null,
        date: null,
        channel,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  public async installUpdate(rawChannel: string): Promise<boolean> {
    if (!app.isPackaged) {
      console.warn('[updater] Cannot install updates in development mode');
      return false;
    }

    try {
      if (!this.updateDownloaded) {
        await autoUpdater.downloadUpdate();
      }
      autoUpdater.quitAndInstall(false, true);
      return true;
    } catch (err) {
      console.error('[updater] Failed to download or install update:', err);
      return false;
    }
  }
}
