/** HTTP client for the audio module. Mirrors `backend/modules/audio/routes.py`. */

import { apiDelete, apiGet, apiPost, apiPut } from '../../api';
import type { AudioStatus, HostMixer, MixerState } from './types';

/** What this machine can do about routing, plus the host mixer's current state. */
export function getAudioStatus(): Promise<AudioStatus> {
  return apiGet<AudioStatus>('/audio/status');
}

/** The dashboard's own routing matrix. */
export function getMixerState(): Promise<MixerState> {
  return apiGet<MixerState>('/audio/mixer');
}

export function saveMixerState(state: MixerState): Promise<MixerState> {
  return apiPut<MixerState>('/audio/mixer', state);
}

export function resetMixerState(): Promise<MixerState> {
  return apiDelete<MixerState>('/audio/mixer');
}

/**
 * Start Voicemeeter. Disruptive on purpose — it takes over the machine's default
 * audio devices — so it is never called implicitly.
 */
export function launchHostMixer(kindId?: number): Promise<AudioStatus> {
  return apiPost<AudioStatus>('/audio/host/launch', { kindId: kindId ?? null });
}

/** Flip one cell of the machine-wide matrix. */
export function setHostSend(strip: number, bus: string, enabled: boolean): Promise<HostMixer> {
  return apiPost<HostMixer>('/audio/host/send', { strip, bus, enabled });
}

/** Set gain and/or mute on a host strip or bus. */
export function setHostLevel(
  target: 'strip' | 'bus',
  index: number,
  changes: { gainDb?: number; muted?: boolean },
): Promise<HostMixer> {
  return apiPost<HostMixer>('/audio/host/level', {
    target,
    index,
    gainDb: changes.gainDb ?? null,
    muted: changes.muted ?? null,
  });
}

/** Create a virtual cable. Linux only; elsewhere this 501s with an install hint. */
export function createVirtualDevice(label: string): Promise<{ id: string; name: string }> {
  return apiPost<{ id: string; name: string }>('/audio/devices', { label });
}

export function destroyVirtualDevice(id: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/audio/devices/${encodeURIComponent(id)}`);
}
