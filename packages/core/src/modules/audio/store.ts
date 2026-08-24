/**
 * The mixer's client state: one per browser, shared by every pane.
 *
 * The engine (`engine.ts`) owns the *graph*; this owns the *document* and keeps
 * three copies of it in agreement — the server's row, this store, and the live
 * graph. The rule is the karaoke one: **the server holds intent, a pane
 * renders it.** A change made here is saved and broadcast; a change made
 * elsewhere (the agent, a second window, a phone on the fabric) arrives on the
 * `audio` channel and is applied without anyone touching this pane.
 *
 * State lives here rather than in a component because a workspace switch
 * unmounts panes, and the mixer must keep mixing while its pane is closed —
 * that is the whole point of the routing being a property of the app rather
 * than of a window.
 */

import { subscribeChannel, type WsMessage } from '../../ws';
import { getMixerState, resetMixerState, saveMixerState } from './api';
import { listInputs, listOutputs, resolveDeviceId } from './devices';
import { mixer } from './engine';
import { SHARE_SINK_DEVICE, type MixerState, type StripState } from './types';

let state: MixerState | null = null;
let outputs: MediaDeviceInfo[] = [];
let inputs: MediaDeviceInfo[] = [];
let loaded = false;
let version = 0;

const listeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const listener of listeners) listener();
}

export function subscribeMixer(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Version counter — the `useSyncExternalStore` snapshot. */
export function mixerVersion(): number {
  return version;
}

export function getState(): MixerState | null {
  return state;
}

export function getOutputs(): MediaDeviceInfo[] {
  return outputs;
}

export function getInputs(): MediaDeviceInfo[] {
  return inputs;
}

/**
 * Load the saved matrix and start mixing.
 *
 * Idempotent: several panes may mount at once, and the boot path calls it too.
 */
export async function ensureLoaded(): Promise<void> {
  if (loaded) return;
  loaded = true;
  try {
    state = await getMixerState();
  } catch {
    // Backend down. The engine still runs on defaults, so audio works — it just
    // is not persisted. Failing closed here would mean no sound at all.
    state = { version: 1, buses: [], strips: [], inputDeviceId: '', inputDeviceLabel: '' };
  }
  await refreshDevices();
  mixer.apply(state, outputs);
  watchDeviceChanges();
  emit();
}

/** Re-read the OS device lists. */
export async function refreshDevices(): Promise<void> {
  [outputs, inputs] = await Promise.all([listOutputs(), listInputs()]);
  mixer.setDevices(outputs);
  emit();
}

let watching = false;

/**
 * React to devices appearing and disappearing.
 *
 * Not a nicety: unplugging the headphones a bus points at leaves that bus
 * pointing at nothing, and the engine's fallback only runs when it is told to
 * re-resolve. Without this, pulling a USB headset silences one bus until reload.
 */
function watchDeviceChanges(): void {
  if (watching || !navigator.mediaDevices) return;
  watching = true;
  navigator.mediaDevices.addEventListener('devicechange', () => {
    void refreshDevices();
  });
}

/** Persist and broadcast. The engine is updated first so the change is audible
 * immediately rather than after a round trip. */
async function commit(next: MixerState): Promise<void> {
  state = next;
  mixer.apply(next, outputs);
  emit();
  try {
    await saveMixerState(next);
  } catch {
    // Keep the local change: the user can hear that it worked, and telling them
    // it did not would be the lie. It will be re-saved on the next edit.
  }
}

/** Flip one matrix cell. */
export async function setSend(stripId: string, busId: string, enabled: boolean): Promise<void> {
  if (!state) return;
  const next = structuredClone(state);
  const strip = next.strips.find((s) => s.id === stripId);
  if (!strip) return;
  strip.sends[busId] = enabled;
  await commit(next);
}

export async function setStripLevel(
  stripId: string,
  changes: Partial<Pick<StripState, 'gain' | 'muted'>>,
): Promise<void> {
  if (!state) return;
  const next = structuredClone(state);
  const strip = next.strips.find((s) => s.id === stripId);
  if (!strip) return;
  Object.assign(strip, changes);
  await commit(next);
}

export async function setBusLevel(
  busId: string,
  changes: { gain?: number; muted?: boolean },
): Promise<void> {
  if (!state) return;
  const next = structuredClone(state);
  const bus = next.buses.find((b) => b.id === busId);
  if (!bus) return;
  Object.assign(bus, changes);
  await commit(next);
}

/** Point a bus at an output device. Stores the label alongside the id — see
 * `resolveDeviceId` for why the label is not decoration. */
export async function setBusDevice(busId: string, deviceId: string): Promise<void> {
  if (!state) return;
  const next = structuredClone(state);
  const bus = next.buses.find((b) => b.id === busId);
  if (!bus) return;
  const device = outputs.find((d) => d.deviceId === deviceId);
  bus.deviceId = deviceId;
  bus.deviceLabel = device?.label ?? '';
  await commit(next);
}

/** Choose the microphone. Same id+label pairing, same reason. */
export async function setInputDevice(deviceId: string): Promise<void> {
  if (!state) return;
  const next = structuredClone(state);
  const device = inputs.find((d) => d.deviceId === deviceId);
  next.inputDeviceId = deviceId;
  next.inputDeviceLabel = device?.label ?? '';
  await commit(next);
}

/**
 * The `audio` constraints every microphone capture in the app should use.
 *
 * This is the "default input device" half of the feature, and it only works if
 * *every* caller goes through here — a module that keeps calling
 * `getUserMedia({ audio: true })` silently gets the system default no matter
 * what the user picked, and there is nothing in the UI to reveal it.
 *
 * Merges caller constraints (echo cancellation, channel count) rather than
 * replacing them: those are per-use-case decisions the audio module has no
 * business overriding.
 */
export function inputConstraints(extra: MediaTrackConstraints = {}): MediaTrackConstraints {
  const saved = state?.inputDeviceId ?? '';
  const resolved = resolveDeviceId(saved, state?.inputDeviceLabel ?? '', inputs);
  // `exact` is deliberately not used: a device that has gone missing should fall
  // back to the default microphone, not make the capture fail outright.
  return resolved ? { ...extra, deviceId: resolved } : extra;
}

/** Add an output. New buses start with nothing routed to them — adding an
 * output must not move audio on its own. */
export async function addBus(label: string, deviceId: string): Promise<void> {
  if (!state) return;
  const next = structuredClone(state);
  const used = new Set(next.buses.map((b) => b.id));
  let index = 1;
  while (used.has(`A${index}`)) index += 1;
  const device = outputs.find((d) => d.deviceId === deviceId);
  next.buses.push({
    id: `A${index}`,
    label,
    deviceId,
    deviceLabel: device?.label ?? '',
    gain: 0,
    muted: false,
    virtual: false,
  });
  for (const strip of next.strips) strip.sends[`A${index}`] = false;
  await commit(next);
}

/**
 * The id of the bus that feeds a shared session's viewers. Fixed rather than
 * allocated like `A1`/`A2`, because the share module has to find it again.
 */
export const SHARE_BUS_ID = 'VIEWERS';

/**
 * Make sure the viewers' bus exists, and return its id — or `null` if the mixer
 * document could not be established at all.
 *
 * Starts with **nothing routed to it**, the same rule `addBus` follows and for a
 * stronger reason: this output is another person's ears. Defaulting it to carry
 * whatever the host is playing would make starting a screen share silently
 * broadcast their music, their notifications and anything else already on a
 * strip. The share pane says so, so silence reads as a choice rather than a bug.
 *
 * `ensureLoaded` is awaited rather than assumed: sharing a screen can be the
 * first thing in a session to want a bus, and nothing on that path mounts the
 * mixer pane. Returning early on an unloaded document — as this used to — left
 * the bus uncreated and the stream went out with no audio path whatsoever,
 * while the pane told the host to route a strip into a bus that did not exist.
 */
export async function ensureShareBus(): Promise<string | null> {
  await ensureLoaded();
  // Also subscribe to the `audio` channel. `ensureLoaded` only fetches the
  // document once; without this, a share started with no mixer pane open never
  // hears about a routing made from the agent, a phone on the fabric or a second
  // window, and reports "no audio" for a stream that is carrying sound.
  connectAudio();
  if (!state) return null;
  if (state.buses.some((b) => b.id === SHARE_BUS_ID)) return SHARE_BUS_ID;
  const next = structuredClone(state);
  next.buses.push({
    id: SHARE_BUS_ID,
    label: 'Viewers',
    deviceId: SHARE_SINK_DEVICE,
    deviceLabel: '',
    gain: 0,
    muted: false,
    // Not a virtual cable: `virtual` labels a bus that feeds another *application*
    // on this machine, and this one feeds people on other machines entirely.
    virtual: false,
  });
  for (const strip of next.strips) strip.sends[SHARE_BUS_ID] = false;
  await commit(next);
  return SHARE_BUS_ID;
}

/**
 * Whether anything is actually routed into a bus.
 *
 * Deliberately not "does the bus have an audio track". A
 * `MediaStreamAudioDestinationNode` always carries exactly one track, silent or
 * not, so track-counting reports every share as "screen + audio" the moment the
 * viewers' bus exists — which, now that `ensureShareBus` always creates it, is
 * always. Reading the routing document instead is the only way to tell sound
 * from silence, and a share that claims audio it is not sending is the bug the
 * share pane's hint exists to prevent.
 *
 * Pure and state-passed so it can be tested without a Web Audio implementation.
 */
export function busHasSource(mixerState: MixerState | null, busId: string): boolean {
  if (!mixerState) return false;
  // A muted strip is still a routing the host made; it is one click from
  // audible, and the mixer already shows it as muted. Only an off send means
  // nothing is going there.
  return mixerState.strips.some((strip) => strip.sends[busId] === true);
}

/** Whether anything is routed to the viewers' bus right now. */
export function shareBusHasSource(): boolean {
  return busHasSource(state, SHARE_BUS_ID);
}

export async function removeBus(busId: string): Promise<void> {
  if (!state || state.buses.length <= 1) return;
  const next = structuredClone(state);
  next.buses = next.buses.filter((b) => b.id !== busId);
  for (const strip of next.strips) delete strip.sends[busId];
  await commit(next);
}

export async function resetMixer(): Promise<void> {
  const next = await resetMixerState();
  state = next;
  mixer.apply(next, outputs);
  emit();
}

/**
 * Apply a matrix that arrived from somewhere else.
 *
 * Saved deliberately *not* re-sent: this came from the server, and echoing it
 * back would make two panes ping-pong a document forever.
 */
function applyRemote(next: MixerState): void {
  state = next;
  mixer.apply(next, outputs);
  emit();
}

let connected = false;

/** Subscribe to the `audio` channel so remote changes land here. */
export function connectAudio(): void {
  if (connected) return;
  connected = true;
  subscribeChannel('audio', (message: WsMessage) => {
    if (message.event === 'mixer') applyRemote(message.data as MixerState);
  });
}
