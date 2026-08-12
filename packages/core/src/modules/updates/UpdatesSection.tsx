/**
 * Settings-page section for remote updates: which channel, what version is
 * running, and the check/install buttons.
 *
 * The three states it must keep distinct — the same distinction the hardware
 * probe makes, for the same reason — are **up to date**, **could not ask**, and
 * **not applicable** (the browser layout has nothing to update). Collapsing the
 * middle one into the first is how a user ends up believing they are current
 * while the check has been failing for a month.
 */
import { useState } from 'react';

import { useSetting } from '../../settings';
import { checkForUpdate, installUpdate, updatesSupported, type UpdateInfo } from './api';

export function UpdatesSection() {
  const channel = useSetting<string>('app.releaseChannel') ?? 'stable';
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState('');

  const supported = updatesSupported();

  const check = async (): Promise<void> => {
    setBusy('checking');
    try {
      setInfo(await checkForUpdate(channel));
    } finally {
      setBusy('');
    }
  };

  const install = async (): Promise<void> => {
    setBusy('installing');
    try {
      // On success the app restarts and this never resolves.
      const started = await installUpdate(channel);
      if (!started) setInfo(await checkForUpdate(channel));
    } catch (exc: unknown) {
      setInfo((prev) => (prev ? { ...prev, error: String(exc) } : prev));
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="updates-section">
      <div className="setting-row">
        <div className="setting-label">
          <label>Updates</label>
          <p className="setting-desc">
            {supported
              ? 'Checks the release channel’s signed manifest. An update whose signature does not verify is refused, not installed with a warning. Your data directory — downloaded llama.cpp builds, GGUFs, traces, libraries — is versioned separately and is never touched by an update.'
              : 'The browser layout is served fresh on every load, so there is nothing here to update. Open the desktop app to manage its version.'}
          </p>
        </div>
        {supported ? (
          <button className="setting-button" onClick={() => void check()} disabled={busy !== ''}>
            {busy === 'checking' ? 'Checking…' : 'Check now'}
          </button>
        ) : null}
      </div>

      {supported && info ? (
        <div className="updates-result">
          <p>
            Running <code>{info.currentVersion}</code> on the <strong>{info.channel}</strong>{' '}
            channel.
          </p>
          {info.error ? (
            <p className="updates-unknown">
              Could not check for updates: {info.error}. This is not the same as being up to date.
            </p>
          ) : info.available ? (
            <>
              <p className="updates-available">
                <strong>{info.version}</strong> is available
                {info.date ? ` (${info.date})` : ''}.
              </p>
              {info.notes ? <pre className="updates-notes">{info.notes}</pre> : null}
              <button
                className="setting-button"
                onClick={() => void install()}
                disabled={busy !== ''}
              >
                {busy === 'installing' ? 'Installing…' : 'Install and restart'}
              </button>
            </>
          ) : (
            <p>Up to date.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
