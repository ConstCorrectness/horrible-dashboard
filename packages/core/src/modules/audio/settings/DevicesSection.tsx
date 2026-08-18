/**
 * The audio settings section: what this machine can do, stated honestly.
 *
 * The reason this is a component rather than three `SettingDecl`s is the same
 * reason the hardware module has one: the interesting answer has **three**
 * states, not two. "No virtual audio device" and "we could not check for one"
 * are different facts, and rendering the second as the first tells a user with a
 * working Voicemeeter to go install Voicemeeter.
 */

import { useCallback, useEffect, useState } from 'react';

import { getAudioStatus } from '../api';
import { canChooseOutput, hasDeviceLabels, requestDeviceLabels } from '../devices';
import { getInputs, getOutputs, refreshDevices, ensureLoaded } from '../store';
import type { AudioStatus } from '../types';

export function AudioDevicesSection() {
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([]);
  const [inputs, setInputs] = useState<MediaDeviceInfo[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    await ensureLoaded();
    await refreshDevices();
    setOutputs(getOutputs());
    setInputs(getInputs());
    try {
      setStatus(await getAudioStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const grant = useCallback(async () => {
    setBusy(true);
    await requestDeviceLabels();
    await load();
    setBusy(false);
  }, [load]);

  const labelsHidden = outputs.length > 0 && !hasDeviceLabels(outputs);

  return (
    <div className="mx" style={{ overflow: 'visible' }}>
      <div className="mx__section">
        <h3 className="mx__title">Devices</h3>
        {labelsHidden ? (
          <div className="mx__note">
            {outputs.length} output{outputs.length === 1 ? '' : 's'} and {inputs.length} input
            {inputs.length === 1 ? '' : 's'} were found, but your browser hides their names until a
            microphone permission is granted.
            <div className="mx__actions">
              <button className="mx__btn" onClick={grant} disabled={busy}>
                Show device names
              </button>
            </div>
          </div>
        ) : (
          <div className="mx__note">
            {outputs.length} output{outputs.length === 1 ? '' : 's'}, {inputs.length} input
            {inputs.length === 1 ? '' : 's'}.{' '}
            {canChooseOutput()
              ? 'Audio can be sent to a specific output.'
              : 'This browser cannot target a specific output; everything plays to the system default.'}
          </div>
        )}
      </div>

      <div className="mx__section">
        <h3 className="mx__title">Routing to other applications</h3>
        {status === null ? (
          <div className="mx__note">
            The backend did not answer, so whether this machine has a virtual audio device is{' '}
            <strong>unknown</strong>.
          </div>
        ) : !status.provider.certain ? (
          <div className="mx__note">
            <strong>Unknown.</strong> {status.provider.note}
          </div>
        ) : (
          <div className="mx__note">
            {status.provider.note}
            {status.provider.devices.length > 0 && (
              <div style={{ marginTop: 6 }}>
                Virtual devices: {status.provider.devices.map((d) => d.name).join(', ')}
              </div>
            )}
            {!status.provider.installed && status.provider.installUrl && (
              <div className="mx__actions">
                <a
                  className="mx__btn"
                  href={status.provider.installUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  Get {status.provider.installName}
                </a>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
