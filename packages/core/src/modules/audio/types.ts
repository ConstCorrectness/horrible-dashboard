/**
 * The mixer's vocabulary. Two axes and the matrix between them.
 *
 * The shape is VoiceMeeter's, because the problem is VoiceMeeter's: a **strip**
 * is a source of sound, a **bus** is somewhere sound comes out, and a strip may
 * feed any number of buses at once. That last clause is the entire feature. A
 * single "output device" setting cannot express "play this into my microphone
 * *and* into my headphones", which is the thing people actually want.
 */

/**
 * A source of audio, declared by whichever module produces it.
 *
 * Declared rather than created: the karaoke module knows it makes sound long
 * before the user opens the mixer, and the mixer must be able to show a fader
 * for a source that has not started yet. `connect` is what turns a declaration
 * into live audio, and is called by the producing module when it has a node.
 */
export interface StripDecl {
  /** Stable id. Persisted in the routing matrix, so renaming one loses its routing. */
  id: string;
  /** Shown on the fader. */
  label: string;
  /** Optional emoji shown in the mixer, matching the module's own icon. */
  icon?: string;
  /**
   * True for the microphone strip. The mixer treats it differently in exactly
   * one way — it is the strip whose *input device* is selectable — and marking
   * it here beats matching on the id.
   */
  isInput?: boolean;
  /**
   * Which buses this strip feeds when nothing is saved. Defaults to the first
   * bus, which reproduces the behaviour of an app with no mixer: everything to
   * one output. Installing the mixer must not change what anyone hears.
   */
  defaultSends?: string[];
}

/**
 * The `deviceId` of the bus that feeds a shared session's viewers.
 *
 * A sentinel rather than a real device id, because there is no device: the bus
 * ends in a `MediaStreamDestination` that a WebRTC sender reads. It lives in the
 * matrix as an ordinary output so that "what the viewers hear" is a column the
 * user can route any strip into — which is the whole reason this module has a
 * matrix instead of an output setting. Karaoke to the viewers but not to your
 * headphones, or your microphone to both, are one checkbox each.
 *
 * Namespaced with a scheme a real `MediaDeviceInfo.deviceId` cannot collide with.
 */
export const SHARE_SINK_DEVICE = 'horrible:viewers';

/** An output. A bus with no `deviceId` is the system default. */
export interface BusConfig {
  id: string;
  label: string;
  /**
   * `MediaDeviceInfo.deviceId`. Empty means the system default, which is also
   * the fallback whenever a saved device has gone away.
   */
  deviceId: string;
  /**
   * The device's label at the time it was chosen. **Load-bearing, not
   * cosmetic**: `deviceId` is scoped to the browser origin and is regenerated
   * when site data is cleared, so on the next boot the saved id can name
   * nothing. The label is what lets us find the same headphones again. See
   * `resolveDeviceId`.
   */
  deviceLabel: string;
  /** Fader position in dB. 0 is unity. */
  gain: number;
  muted: boolean;
  /**
   * Whether this bus points at a virtual cable — i.e. whether sending to it
   * means "another application hears this". Derived from the device name, and
   * only ever used to *label* the bus, never to change routing.
   */
  virtual: boolean;
}

/** One row of the matrix: a strip's settings and its sends. */
export interface StripState {
  id: string;
  label: string;
  gain: number;
  muted: boolean;
  /** Bus id → whether this strip feeds it. */
  sends: Record<string, boolean>;
}

/** The whole persisted mixer. Mirrors `MixerStateModel` on the backend. */
export interface MixerState {
  version: number;
  buses: BusConfig[];
  strips: StripState[];
  /** The microphone. Same id/label pairing as a bus, for the same reason. */
  inputDeviceId: string;
  inputDeviceLabel: string;
}

/** A live audio source handed back to the module that registered it. */
export interface StripHandle {
  id: string;
  /**
   * Connect your audio into this node. It is the strip's fader input — anything
   * connected here is routed by the matrix rather than going straight out.
   */
  input: GainNode;
  /** The one shared context. Nodes from a different context cannot connect. */
  context: AudioContext;
  /** Remove the strip's live audio. The fader and its routing are kept. */
  release: () => void;
}

/** What the backend reports about virtual audio on this machine. */
export interface ProviderStatus {
  platform: string;
  provider: string | null;
  installed: boolean;
  running: boolean;
  /** False means *we could not ask*. Must never be rendered as "not installed". */
  certain: boolean;
  canCreate: boolean;
  canControl: boolean;
  note: string;
  installName: string;
  installUrl: string | null;
  devices: { id: string; name: string; kind: string; owned: boolean }[];
}

/** One strip of the machine-wide (Voicemeeter) mixer. */
export interface HostStrip {
  index: number;
  name: string;
  label: string;
  isVirtual: boolean;
  gain: number;
  muted: boolean;
  sends: Record<string, boolean>;
}

export interface HostBus {
  index: number;
  name: string;
  label: string;
  isVirtual: boolean;
  gain: number;
  muted: boolean;
}

export interface HostMixer {
  kind: string;
  kindId: number;
  version: string;
  strips: HostStrip[];
  buses: HostBus[];
}

export interface AudioStatus {
  provider: ProviderStatus;
  host: HostMixer | null;
  hostError: string | null;
}
