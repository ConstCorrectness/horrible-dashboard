/**
 * Settings-page section showing **what the probe found** and **what it chose**.
 *
 * The override controls next to this section are ordinary declarative settings;
 * this exists for the half that cannot be a form field — the reading itself.
 * Two rules it renders and must keep rendering:
 *
 * - **"Unknown" is not "none".** With `certain: false` the accelerator line says
 *   the probe could not ask, and shows the note explaining why. Printing "no GPU
 *   detected" there would be the app telling a user with a GPU that they have none.
 * - **Every default carries its reason.** A CPU build chosen on a machine whose
 *   owner knows it has a card is otherwise inexplicable, and the reason string is
 *   the whole difference between a bug report and a settings change.
 *
 * See docs/modules/hardware.mdx.
 */
import { useCallback, useEffect, useState } from 'react';

import { getHardware, refreshHardware, type Accelerator, type Hardware } from './api';

function gb(mb: number | null): string {
  if (mb === null) return 'unknown';
  return `${(mb / 1024).toFixed(mb >= 10_240 ? 0 : 1)} GB`;
}

function describe(accelerator: Accelerator): string {
  const memory =
    accelerator.vramMb === null
      ? 'memory not reported'
      : accelerator.unified
        ? `${gb(accelerator.vramMb)} unified (shared with the CPU)`
        : `${gb(accelerator.vramMb)} VRAM`;
  return `${accelerator.name} — ${memory}`;
}

export function MachineSection() {
  const [hardware, setHardware] = useState<Hardware | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    void getHardware()
      .then(setHardware)
      .catch((exc: unknown) => setError(String(exc)));
  }, []);
  useEffect(load, [load]);

  const reprobe = async (): Promise<void> => {
    setBusy(true);
    setError('');
    try {
      setHardware(await refreshHardware());
    } catch (exc: unknown) {
      setError(String(exc));
    } finally {
      setBusy(false);
    }
  };

  const profile = hardware?.profile;
  const defaults = hardware?.defaults;

  return (
    <div className="machine-section">
      <div className="setting-row">
        <div className="setting-label">
          <label>This machine</label>
          <p className="setting-desc">
            One probe, run once per process, deciding the llama.cpp build, GPU offload, thread count
            and activation-trace cap. Override it with the settings below and re-probe for them to
            take effect.
          </p>
        </div>
        <button className="setting-button" onClick={() => void reprobe()} disabled={busy}>
          {busy ? 'Probing…' : 'Re-probe'}
        </button>
      </div>

      {error ? <p className="machine-error">⚠ {error}</p> : null}

      {profile && defaults ? (
        <>
          <dl className="machine-facts">
            <dt>Platform</dt>
            <dd>
              {profile.os} / {profile.arch} · {profile.cpuCount} logical CPUs · {gb(profile.ramMb)}{' '}
              RAM
              {profile.ramMb !== null && !profile.ramExact ? ' (estimated)' : ''}
            </dd>

            <dt>Accelerator</dt>
            <dd>
              {profile.accelerators.length > 0 ? (
                <ul className="machine-accelerators">
                  {profile.accelerators.map((accelerator) => (
                    <li key={`${accelerator.kind}:${accelerator.name}`}>
                      <span className="machine-kind">{accelerator.kind}</span>{' '}
                      {describe(accelerator)}
                      <span className="machine-source">
                        {' '}
                        via {accelerator.detectedBy}
                        {accelerator.exact ? '' : ', not measured'}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : profile.certain ? (
                'None. This machine will run on the CPU.'
              ) : (
                <span className="machine-unknown">
                  Unknown — the probe could not ask. This is not the same as “none”.
                </span>
              )}
              {profile.overridden ? (
                <p className="machine-overridden">Set by you in settings, not measured.</p>
              ) : null}
            </dd>
          </dl>

          {profile.notes.length > 0 ? (
            <ul className="machine-notes">
              {profile.notes.map((note) => (
                <li key={note.kind}>
                  <strong>{note.kind}</strong>: {note.reason}
                </li>
              ))}
            </ul>
          ) : null}

          <table className="machine-defaults">
            <thead>
              <tr>
                <th>Default</th>
                <th>Value</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>llama.cpp build</td>
                <td>{defaults.llamaVariant}</td>
                <td>{defaults.reasons.llamaVariant ?? ''}</td>
              </tr>
              <tr>
                <td>GPU layers</td>
                <td>{defaults.gpuLayers === 999 ? 'all' : defaults.gpuLayers}</td>
                <td>{defaults.reasons.gpuLayers ?? ''}</td>
              </tr>
              <tr>
                <td>Threads</td>
                <td>{defaults.threads}</td>
                <td>{defaults.reasons.threads ?? ''}</td>
              </tr>
              <tr>
                <td>Trace token cap</td>
                <td>{defaults.traceTokenCap}</td>
                <td>{defaults.reasons.traceTokenCap ?? ''}</td>
              </tr>
              <tr>
                <td>Local training</td>
                <td>{defaults.localTraining ? 'recommended' : 'not recommended'}</td>
                <td>{defaults.reasons.localTraining ?? ''}</td>
              </tr>
            </tbody>
          </table>
        </>
      ) : (
        <p className="setting-desc">Probing…</p>
      )}
    </div>
  );
}
