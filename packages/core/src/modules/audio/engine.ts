/**
 * The mixer graph: one shared AudioContext, N strips, M buses, a matrix between.
 *
 * ## Why one context
 *
 * Web Audio nodes cannot cross contexts — a `MediaStreamAudioSourceNode` made in
 * one `AudioContext` will not connect to a `GainNode` from another, and the
 * failure is a silent no-op, not an exception. Every module in this app used to
 * make its own context, which is exactly why none of their audio could be routed
 * together. So there is one context here and `getContext()` is how a module
 * gets it. A module that keeps calling `new AudioContext()` is not in the mixer,
 * and nothing will tell it so.
 *
 * ## How a bus reaches a device
 *
 * Two shapes, chosen per bus:
 *
 * - **System default** (`deviceId === ''`): connected straight to
 *   `ctx.destination`. This is the low-latency path and, crucially, the path
 *   that needs no permissions — a fresh install sounds exactly like the app did
 *   before the mixer existed.
 * - **A chosen device**: the bus feeds a `MediaStreamAudioDestinationNode`,
 *   whose stream is played by a hidden `<audio>` element with `setSinkId()`.
 *   That element is the only way to aim audio at a *specific* output; an
 *   `AudioContext` has just one sink, so N outputs need N elements.
 *
 * ## Three things that fail silently here
 *
 * 1. **The context starts suspended.** Browsers refuse to start audio without a
 *    user gesture. A suspended context produces no sound and throws nothing, so
 *    `unlockOnGesture` resumes it on the first interaction.
 * 2. **An `<audio>` element playing a MediaStream must stay referenced.** If the
 *    only reference is dropped, Chromium can garbage-collect it mid-playback and
 *    the bus goes quiet. They are held in `buses`.
 * 3. **`setSinkId` rejects asynchronously.** A device that has been unplugged
 *    since it was saved rejects the promise while the element happily plays to
 *    the default output — so the failure is caught and the bus is marked as
 *    fallen back, rather than lying about where the audio went.
 */

import { canChooseOutput, resolveDeviceId } from './devices';
import type { BusConfig, MixerState, StripDecl, StripHandle } from './types';

/** dB → linear amplitude. -60 dB is the fader floor and is treated as silence. */
export function dbToGain(db: number): number {
  if (db <= -60) return 0;
  return Math.pow(10, db / 20);
}

interface LiveStrip {
  decl: StripDecl;
  /** What the producing module connects into: the strip's fader. */
  fader: GainNode;
  /** One gain per bus. 0 or 1 — the matrix cell. */
  sends: Map<string, GainNode>;
  /** True once a module has actually connected audio to `fader`. */
  live: boolean;
}

interface LiveBus {
  config: BusConfig;
  gain: GainNode;
  sink: MediaStreamAudioDestinationNode | null;
  element: HTMLAudioElement | null;
  /** Set when the chosen device could not be used and audio went to default. */
  fellBack: boolean;
}

type Listener = () => void;

class MixerEngine {
  private ctx: AudioContext | null = null;
  private strips = new Map<string, LiveStrip>();
  private buses = new Map<string, LiveBus>();
  private decls = new Map<string, StripDecl>();
  private state: MixerState | null = null;
  private devices: MediaDeviceInfo[] = [];
  private listeners = new Set<Listener>();
  private gestureBound = false;

  // -- lifecycle ---------------------------------------------------------

  /**
   * The one shared AudioContext, created on first use.
   *
   * Never at module scope: importing this file must stay free of side effects,
   * or a unit test (and the plugin loader) constructs an AudioContext in an
   * environment that has none.
   */
  getContext(): AudioContext {
    if (this.ctx) return this.ctx;
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new Ctor();
    this.unlockOnGesture();
    return this.ctx;
  }

  /** Whether a context exists yet — lets the UI avoid creating one just to render. */
  isStarted(): boolean {
    return this.ctx !== null;
  }

  /**
   * Resume the context on the first user gesture.
   *
   * Autoplay policy suspends a context created without one, and a suspended
   * context is silent with no error anywhere. `once` on each listener plus the
   * `gestureBound` guard keeps this to a single resume attempt per event kind.
   */
  private unlockOnGesture(): void {
    if (this.gestureBound || typeof window === 'undefined') return;
    this.gestureBound = true;
    const resume = () => {
      void this.ctx?.resume();
    };
    for (const event of ['pointerdown', 'keydown'] as const) {
      window.addEventListener(event, resume, { once: true, passive: true });
    }
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  // -- strips ------------------------------------------------------------

  /**
   * Declare a source of audio. Safe to call before the mixer has any state and
   * before the module has any sound to play — the fader exists either way, which
   * is what lets the mixer show a channel for something not yet playing.
   */
  declareStrip(decl: StripDecl): void {
    this.decls.set(decl.id, decl);
    if (this.state) this.ensureStripState(decl);
    this.emit();
  }

  /** Every declared source, whether or not it is currently making sound. */
  declarations(): StripDecl[] {
    return [...this.decls.values()];
  }

  /** Whether a strip currently has audio connected to it. */
  isLive(id: string): boolean {
    return this.strips.get(id)?.live ?? false;
  }

  /**
   * Get the node to connect audio into.
   *
   * This is the whole contract for a producing module: call it, connect your
   * source to `handle.input`, and never touch `ctx.destination` again. Where the
   * sound comes out is now the user's decision, not the module's.
   */
  connectStrip(id: string): StripHandle {
    const ctx = this.getContext();
    const decl = this.decls.get(id) ?? { id, label: id };
    if (!this.decls.has(id)) this.declareStrip(decl);

    let strip = this.strips.get(id);
    if (!strip) {
      strip = { decl, fader: ctx.createGain(), sends: new Map(), live: false };
      this.strips.set(id, strip);
      this.wireStrip(strip);
    }
    strip.live = true;
    this.emit();

    return {
      id,
      input: strip.fader,
      context: ctx,
      release: () => {
        const entry = this.strips.get(id);
        if (!entry) return;
        entry.live = false;
        // The fader and its sends stay: the routing is the user's setting, and
        // a pane closing must not discard it. Only the *source* went away.
        this.emit();
      },
    };
  }

  /** Connect a strip's fader to one send gain per bus. */
  private wireStrip(strip: LiveStrip): void {
    for (const [busId, bus] of this.buses) {
      if (strip.sends.has(busId)) continue;
      const send = this.getContext().createGain();
      send.gain.value = this.sendValue(strip.decl.id, busId);
      strip.fader.connect(send);
      send.connect(bus.gain);
      strip.sends.set(busId, send);
    }
  }

  // -- state -------------------------------------------------------------

  /**
   * Apply a whole mixer state: rebuild buses that changed, add missing strips,
   * and set every fader and matrix cell.
   *
   * Whole-document rather than per-field because that is how the state arrives
   * (from the server, from another pane's broadcast, from the agent). A partial
   * apply would leave the graph and the saved document disagreeing about where
   * audio goes, which is the one inconsistency with an audible symptom.
   */
  apply(state: MixerState, devices?: MediaDeviceInfo[]): void {
    this.state = state;
    if (devices) this.devices = devices;

    const wanted = new Set(state.buses.map((b) => b.id));
    for (const [id, bus] of [...this.buses]) {
      if (!wanted.has(id)) {
        this.teardownBus(bus);
        this.buses.delete(id);
        for (const strip of this.strips.values()) {
          strip.sends.get(id)?.disconnect();
          strip.sends.delete(id);
        }
      }
    }
    for (const config of state.buses) this.ensureBus(config);
    for (const decl of this.decls.values()) this.ensureStripState(decl);

    // Every strip needs a send to every bus, including buses added just now.
    for (const strip of this.strips.values()) this.wireStrip(strip);
    this.applyLevels();
    this.emit();
  }

  /** The saved state, or null before the first apply. */
  snapshot(): MixerState | null {
    return this.state;
  }

  /** Add a default row for a strip the saved state has never seen. */
  private ensureStripState(decl: StripDecl): void {
    if (!this.state) return;
    if (this.state.strips.some((s) => s.id === decl.id)) return;
    const first = this.state.buses[0]?.id;
    const sends: Record<string, boolean> = {};
    for (const bus of this.state.buses) {
      // Default: the first bus only. A new source must not appear on a virtual
      // cable the user set up for something else — "installing a module put my
      // game audio into my work call" is the failure this avoids.
      sends[bus.id] = decl.defaultSends ? decl.defaultSends.includes(bus.id) : bus.id === first;
    }
    this.state.strips.push({ id: decl.id, label: decl.label, gain: 0, muted: false, sends });
  }

  private sendValue(stripId: string, busId: string): number {
    const strip = this.state?.strips.find((s) => s.id === stripId);
    return strip?.sends[busId] ? 1 : 0;
  }

  /** Push every fader, mute and matrix cell from state into the graph. */
  private applyLevels(): void {
    if (!this.state) return;
    for (const stripState of this.state.strips) {
      const strip = this.strips.get(stripState.id);
      if (!strip) continue;
      strip.fader.gain.value = stripState.muted ? 0 : dbToGain(stripState.gain);
      for (const [busId, send] of strip.sends) {
        send.gain.value = stripState.sends[busId] ? 1 : 0;
      }
    }
    for (const busState of this.state.buses) {
      const bus = this.buses.get(busState.id);
      if (!bus) continue;
      bus.gain.gain.value = busState.muted ? 0 : dbToGain(busState.gain);
    }
  }

  // -- buses -------------------------------------------------------------

  private ensureBus(config: BusConfig): void {
    const existing = this.buses.get(config.id);
    const target = resolveDeviceId(config.deviceId, config.deviceLabel, this.devices);

    if (existing) {
      const currentTarget = resolveDeviceId(
        existing.config.deviceId,
        existing.config.deviceLabel,
        this.devices,
      );
      existing.config = config;
      // Only rebuild the output when the *resolved* device changed. Rebuilding
      // on every apply would click audibly on every fader move.
      if (currentTarget !== target) this.attachOutput(existing, target);
      return;
    }

    const ctx = this.getContext();
    const bus: LiveBus = {
      config,
      gain: ctx.createGain(),
      sink: null,
      element: null,
      fellBack: false,
    };
    this.buses.set(config.id, bus);
    this.attachOutput(bus, target);
  }

  /** Point a bus at a device (or at the system default when `deviceId` is ''). */
  private attachOutput(bus: LiveBus, deviceId: string): void {
    const ctx = this.getContext();
    bus.gain.disconnect();
    this.teardownElement(bus);
    bus.fellBack = false;

    if (!deviceId || !canChooseOutput()) {
      bus.gain.connect(ctx.destination);
      // A bus asking for a specific device on a browser that cannot target one
      // is a fallback the user must be told about, not a silent downgrade.
      bus.fellBack = Boolean(deviceId) && !canChooseOutput();
      return;
    }

    const sink = ctx.createMediaStreamDestination();
    bus.gain.connect(sink);
    const element = new Audio();
    element.srcObject = sink.stream;
    element.autoplay = true;
    bus.sink = sink;
    bus.element = element;

    const el = element as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> };
    void el
      .setSinkId?.(deviceId)
      .then(() => element.play())
      .catch(() => {
        // The device went away between being saved and being used. Fall back to
        // the default output rather than leaving the bus silent, and record it
        // so the mixer can show which bus is not where it says it is.
        bus.fellBack = true;
        bus.gain.disconnect();
        this.teardownElement(bus);
        bus.gain.connect(ctx.destination);
        this.emit();
      });
  }

  private teardownElement(bus: LiveBus): void {
    if (bus.element) {
      bus.element.pause();
      bus.element.srcObject = null;
      bus.element = null;
    }
    if (bus.sink) {
      bus.sink.disconnect();
      bus.sink = null;
    }
  }

  private teardownBus(bus: LiveBus): void {
    bus.gain.disconnect();
    this.teardownElement(bus);
  }

  /** Buses whose audio is not going where the config says. */
  fallbacks(): string[] {
    return [...this.buses.values()].filter((b) => b.fellBack).map((b) => b.config.id);
  }

  /** Refresh the device list — call when `devicechange` fires. */
  setDevices(devices: MediaDeviceInfo[]): void {
    this.devices = devices;
    if (this.state) this.apply(this.state, devices);
  }
}

/**
 * The one mixer. A module-level singleton because the graph models the machine's
 * audio hardware, of which there is one — the same reasoning that makes the
 * karaoke session process-global on the server.
 */
export const mixer = new MixerEngine();
