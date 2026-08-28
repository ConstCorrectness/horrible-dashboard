/**
 * What the machine is, drawn above the controls whose defaults it decides.
 *
 * This replaces a one-line `MachineLine` that showed `profile.primary` and nothing
 * else. Three things the probe already measured were being thrown away, and each
 * of them is the answer to a question the pane otherwise invites:
 *
 * - **`accelerators[]`, not just `primary`.** A machine with an NVIDIA card and an
 *   integrated Intel GPU reported one of them, so "why did it pick that one" had no
 *   answer on screen.
 * - **`notes[]` — what the probe could not ask.** This is the module's whole reason
 *   for existing and it had no renderer anywhere in the app. An absent `nvidia-smi`
 *   and an absent GPU produce the same empty list and are not the same fact; the
 *   hardware module is careful to distinguish them and the UI then flattened it.
 * - **`ramMb` / `cpuCount`.** The thread default is derived from the core count and
 *   the "leave it in RAM" half of every offload decision is bounded by the RAM.
 *
 * The inherited rule: **"unknown" is never drawn as "none".**
 */
import { Meter } from '../../viz/Meter';
import type { Accelerator, Hardware } from '../hardware/api';
import { formatBytes } from './api';

function gbOf(mb: number): string {
  return formatBytes(mb * 1024 * 1024);
}

function AcceleratorCard({ device, primary }: { device: Accelerator; primary: boolean }) {
  return (
    <li className={`llama-gpu${primary ? ' llama-gpu-primary' : ''}`}>
      <div className="llama-gpu-head">
        <span className="llama-dot llama-dot-on" />
        <b>{device.name}</b>
        <span className="llama-tag">{device.kind}</span>
        {primary && <span className="llama-tag llama-ok">primary</span>}
      </div>
      {device.vramMb === null ? (
        /* Vulkan's summary reports no memory size. Inventing one would be
           inventing the exact number the offload decision keys off. */
        <p className="llama-meta">
          Reports no memory size, so nothing here can say what would fit on it.
        </p>
      ) : (
        <>
          <Meter
            label={`${device.name} memory`}
            total={device.vramMb}
            segments={[{ value: device.vramMb, tone: 'primary', label: gbOf(device.vramMb) }]}
          />
          <p className="llama-meta">
            {gbOf(device.vramMb)} {device.unified ? 'unified' : 'VRAM'}
            {' · '}
            {/* `exact: false` covers unified memory and user overrides alike. Both
                are numbers we were told rather than numbers we measured. */}
            <span title={`Detected by ${device.detectedBy}`}>
              {device.exact ? 'measured' : 'reported'}
            </span>
          </p>
          {device.unified && (
            <p className="llama-why">
              Unified memory: the GPU shares this machine’s RAM, so offloading copies nothing
              and there is no second budget to fit inside.
            </p>
          )}
        </>
      )}
    </li>
  );
}

export function MachineBand({
  hardware,
  onReprobe,
}: {
  hardware: Hardware | null;
  onReprobe: () => void;
}) {
  if (!hardware) return null;
  const { profile, defaults } = hardware;
  const devices = profile.accelerators;

  return (
    <section className="llama-machine">
      <div className="llama-band-head">
        <h4>Machine</h4>
        <span className="llama-meta">
          {profile.os} · {profile.arch} · {profile.cpuCount} cores
          {profile.ramMb !== null && (
            <>
              {' · '}
              {gbOf(profile.ramMb)} RAM{profile.ramExact ? '' : ' (approx.)'}
            </>
          )}
        </span>
        {profile.overridden && (
          <span className="llama-tag" title="These are your settings, not a measurement.">
            your override
          </span>
        )}
        <button className="llama-linkbtn" onClick={onReprobe}>
          Re-probe
        </button>
      </div>

      {devices.length > 0 ? (
        <ul className="llama-gpus">
          {devices.map((device, index) => (
            <AcceleratorCard
              key={`${device.kind}:${device.name}:${index}`}
              device={device}
              primary={device === profile.primary || device.name === profile.primary?.name}
            />
          ))}
        </ul>
      ) : profile.certain ? (
        <p className="llama-meta">
          <span className="llama-dot" /> No accelerator — this machine runs on its CPU.
        </p>
      ) : (
        <p className="llama-meta">
          <span className="llama-dot" /> <b>Accelerator unknown.</b> The probe could not ask,
          which is not the same as none.
        </p>
      )}

      {/* The gaps, said out loud. Without this the two sentences above are
          indistinguishable to a reader, which is the failure this module exists
          to prevent. */}
      {profile.notes.length > 0 && (
        <details className="llama-notes">
          <summary>
            What the probe could not ask <span className="llama-tag">{profile.notes.length}</span>
          </summary>
          <ul>
            {profile.notes.map((note) => (
              <li key={`${note.kind}:${note.reason}`}>
                <code>{note.kind}</code> — {note.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Every auto-resolved field, with the probe's own sentence for each. Two of
          these were reachable only from a tooltip in Settings, three clicks from
          the form whose value they explain. */}
      {Object.keys(defaults.reasons).length > 0 && (
        <details className="llama-notes">
          <summary>Why these defaults</summary>
          <ul>
            {Object.entries(defaults.reasons).map(([field, reason]) => (
              <li key={field}>
                <code>{field}</code> — {reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
