/**
 * The mixer pane: the routing matrix, drawn as a matrix.
 *
 * Buses are columns, sources are rows, and each intersection is a button that
 * either sends or does not. This is the VoiceMeeter layout and it is chosen for
 * the same reason VoiceMeeter chose it: the interesting fact is *which sources
 * share a bus*, and a per-source dropdown cannot show that. It is also the only
 * shape in which the feature people want — one source going to two places at
 * once — looks like what it is rather than like a special case.
 */

import { Fragment, useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import { getAudioStatus, launchHostMixer, setHostSend } from '../api';
import { canChooseOutput, hasDeviceLabels, isVirtualDevice, requestDeviceLabels } from '../devices';
import { mixer } from '../engine';
import {
  addBus,
  connectAudio,
  ensureLoaded,
  getInputs,
  getOutputs,
  getState,
  mixerVersion,
  refreshDevices,
  removeBus,
  setBusDevice,
  setBusLevel,
  setInputDevice,
  setSend,
  setStripLevel,
  subscribeMixer,
} from '../store';
import type { AudioStatus } from '../types';

export function AudioMixerPanel() {
  useSyncExternalStore(subscribeMixer, mixerVersion);
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [needsPermission, setNeedsPermission] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    connectAudio();
    void ensureLoaded();
    void getAudioStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  const outputs = getOutputs();
  const inputs = getInputs();
  const state = getState();

  useEffect(() => {
    // Labels come back empty until a media permission has been granted; that is
    // a prompt to show, not an empty device list.
    setNeedsPermission(outputs.length > 0 && !hasDeviceLabels(outputs));
  }, [outputs]);

  const grantPermission = useCallback(async () => {
    setBusy(true);
    await requestDeviceLabels();
    await refreshDevices();
    setBusy(false);
  }, []);

  const startHost = useCallback(async () => {
    setBusy(true);
    try {
      setStatus(await launchHostMixer());
    } finally {
      setBusy(false);
    }
  }, []);

  if (!state) {
    return <div className="mx__empty">Loading the mixer…</div>;
  }

  const strips = mixer.declarations();
  const fallen = new Set(mixer.fallbacks());
  // Buses are columns; the leading column is the source name, the trailing one
  // its fader.
  const columns = `minmax(120px, 1.4fr) minmax(140px, 1fr) repeat(${state.buses.length}, minmax(44px, 0.5fr))`;

  return (
    <div className="mx">
      {needsPermission && (
        <div className="mx__section">
          <div className="mx__note">
            Your browser hides audio device names until a microphone permission is granted. Nothing
            is recorded — the permission is only what unlocks the device list.
            <div className="mx__actions">
              <button className="mx__btn" onClick={grantPermission} disabled={busy}>
                Show my devices
              </button>
            </div>
          </div>
        </div>
      )}

      {!canChooseOutput() && (
        <div className="mx__section">
          <div className="mx__note mx__note--warn">
            This browser cannot send audio to a chosen output device (<code>setSinkId</code> is
            Chromium-only). Everything plays to the system default. The desktop app can do this.
          </div>
        </div>
      )}

      <div className="mx__section">
        <h3 className="mx__title">Routing</h3>
        <div className="mx__grid" style={{ gridTemplateColumns: columns }}>
          <div className="mx__head">Source</div>
          <div className="mx__head">Level</div>
          {state.buses.map((bus) => (
            <div key={bus.id} className="mx__bus">
              <div className="mx__head" title={bus.deviceLabel || 'System default'}>
                {bus.id} · {bus.label}
                {isVirtualDevice(bus.deviceLabel) && <span className="mx__tag">VIRTUAL</span>}
              </div>
              <select
                value={bus.deviceId}
                onChange={(e) => void setBusDevice(bus.id, e.target.value)}
                disabled={!canChooseOutput()}
              >
                <option value="">System default</option>
                {outputs.map((device) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label || 'Unnamed output'}
                  </option>
                ))}
              </select>
              {fallen.has(bus.id) && (
                <span className="mx__db" title="The chosen device could not be used.">
                  ⚠ default
                </span>
              )}
              <div className="mx__fader">
                <button
                  className={`mx__mute ${bus.muted ? 'mx__mute--on' : ''}`}
                  onClick={() => void setBusLevel(bus.id, { muted: !bus.muted })}
                >
                  M
                </button>
                <input
                  type="range"
                  min={-60}
                  max={12}
                  step={1}
                  value={bus.gain}
                  onChange={(e) => void setBusLevel(bus.id, { gain: Number(e.target.value) })}
                />
              </div>
              {state.buses.length > 1 && (
                <button className="mx__btn" onClick={() => void removeBus(bus.id)}>
                  Remove
                </button>
              )}
            </div>
          ))}

          {strips.length === 0 && (
            <div className="mx__empty" style={{ gridColumn: `1 / span ${state.buses.length + 2}` }}>
              No audio sources yet. Play something — karaoke, a game, the agent's voice — and its
              channel appears here.
            </div>
          )}

          {strips.map((decl) => {
            const stripState = state.strips.find((s) => s.id === decl.id);
            if (!stripState) return null;
            const live = mixer.isLive(decl.id);
            return (
              <ChannelRow
                key={decl.id}
                id={decl.id}
                icon={decl.icon}
                label={decl.label}
                live={live}
                gain={stripState.gain}
                muted={stripState.muted}
                sends={stripState.sends}
                buses={state.buses.map((b) => ({
                  id: b.id,
                  virtual: isVirtualDevice(b.deviceLabel),
                }))}
              />
            );
          })}
        </div>

        <div className="mx__actions">
          <button
            className="mx__btn"
            onClick={() => void addBus(`Output ${state.buses.length + 1}`, '')}
            disabled={!canChooseOutput()}
          >
            Add output
          </button>
        </div>
      </div>

      <div className="mx__section">
        <h3 className="mx__title">Microphone</h3>
        <select
          value={state.inputDeviceId}
          onChange={(e) => void setInputDevice(e.target.value)}
          style={{ width: '100%', maxWidth: 340 }}
        >
          <option value="">System default</option>
          {inputs.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label || 'Unnamed microphone'}
            </option>
          ))}
        </select>
      </div>

      {status && (
        <HostSection status={status} onLaunch={startHost} busy={busy} onChange={setStatus} />
      )}
    </div>
  );
}

interface ChannelRowProps {
  id: string;
  icon?: string;
  label: string;
  live: boolean;
  gain: number;
  muted: boolean;
  sends: Record<string, boolean>;
  buses: { id: string; virtual: boolean }[];
}

function ChannelRow(props: ChannelRowProps) {
  return (
    <>
      <div className={`mx__strip-name ${props.live ? '' : 'mx__strip-name--idle'}`}>
        <span className={`mx__dot ${props.live ? 'mx__dot--live' : ''}`} />
        {props.icon && <span>{props.icon}</span>}
        <span title={props.live ? 'Playing' : 'Registered, not currently playing'}>
          {props.label}
        </span>
      </div>
      <div className="mx__fader">
        <button
          className={`mx__mute ${props.muted ? 'mx__mute--on' : ''}`}
          onClick={() => void setStripLevel(props.id, { muted: !props.muted })}
        >
          M
        </button>
        <input
          type="range"
          min={-60}
          max={12}
          step={1}
          value={props.gain}
          onChange={(e) => void setStripLevel(props.id, { gain: Number(e.target.value) })}
        />
        <span className="mx__db">{props.gain > 0 ? `+${props.gain}` : props.gain} dB</span>
      </div>
      {props.buses.map((bus) => {
        const on = Boolean(props.sends[bus.id]);
        return (
          <div key={bus.id} className="mx__cell">
            <button
              className={`mx__send ${on ? 'mx__send--on' : ''} ${bus.virtual ? 'mx__send--virtual' : ''}`}
              onClick={() => void setSend(props.id, bus.id, !on)}
              title={`${props.label} → ${bus.id}`}
            >
              {bus.id}
            </button>
          </div>
        );
      })}
    </>
  );
}

/**
 * The machine-wide mixer.
 *
 * Kept visually separate from the dashboard's own matrix because the two are not
 * interchangeable and confusing them is the expensive mistake: a cell here moves
 * audio for *every application on the machine*, including ones the dashboard has
 * nothing to do with.
 */
function HostSection({
  status,
  onLaunch,
  busy,
  onChange,
}: {
  status: AudioStatus;
  onLaunch: () => void;
  busy: boolean;
  onChange: (next: AudioStatus) => void;
}) {
  const { provider, host } = status;

  const flip = useCallback(
    async (strip: number, bus: string, enabled: boolean) => {
      const next = await setHostSend(strip, bus, enabled);
      onChange({ ...status, host: next });
    },
    [status, onChange],
  );

  if (!provider.certain) {
    return (
      <div className="mx__section">
        <h3 className="mx__title">Other applications</h3>
        <div className="mx__note">
          Could not check this machine for virtual audio devices, so whether other applications'
          audio can be routed is <strong>unknown</strong>. {provider.note}
        </div>
      </div>
    );
  }

  if (!host) {
    return (
      <div className="mx__section">
        <h3 className="mx__title">Other applications</h3>
        <div className="mx__note">
          {provider.note}
          <div className="mx__actions">
            {provider.installed && provider.provider === 'voicemeeter' && (
              <button className="mx__btn" onClick={onLaunch} disabled={busy}>
                Start Voicemeeter
              </button>
            )}
            {!provider.installed && provider.installUrl && (
              <a className="mx__btn" href={provider.installUrl} target="_blank" rel="noreferrer">
                Get {provider.installName}
              </a>
            )}
          </div>
        </div>
      </div>
    );
  }

  const busNames = host.buses.map((b) => b.name);
  const columns = `minmax(120px, 1.4fr) repeat(${busNames.length}, minmax(40px, 0.5fr))`;

  return (
    <div className="mx__section">
      <h3 className="mx__title">
        Other applications · {host.kind} {host.version}
      </h3>
      <div className="mx__grid" style={{ gridTemplateColumns: columns }}>
        <div className="mx__head">Input</div>
        {host.buses.map((bus) => (
          <div key={bus.index} className="mx__head" title={bus.label}>
            {bus.name}
            {bus.isVirtual && <span className="mx__tag">V</span>}
          </div>
        ))}
        {host.strips.map((strip) => (
          // Keyed Fragment: a bare `<>` in a map has no key, and React reorders
          // rows by position, which in a matrix means send buttons following the
          // wrong strip after any change to the list.
          <Fragment key={strip.index}>
            <div className="mx__strip-name">
              <span className="mx__dot mx__dot--live" />
              {strip.label}
            </div>
            {busNames.map((bus) => {
              const present = bus in strip.sends;
              const on = Boolean(strip.sends[bus]);
              return (
                <div key={`${strip.index}-${bus}`} className="mx__cell">
                  <button
                    className={`mx__send ${on ? 'mx__send--on' : ''} ${bus.startsWith('B') ? 'mx__send--virtual' : ''}`}
                    // A cell this mixer version does not have is absent, not off:
                    // offering a switch that silently does nothing is worse than
                    // not offering one.
                    disabled={!present}
                    onClick={() => void flip(strip.index, bus, !on)}
                  >
                    {bus}
                  </button>
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
