/**
 * The neuron stack's sounds: short sine tones on a pentatonic scale, so hovering
 * across a plate plays a tune rather than a buzz. Routed through the app's one
 * AudioContext (`audio/engine.ts` `mixer`), on its own fader; never closed here.
 * Muting is per viewer, kept in localStorage. Best-effort throughout: a failure
 * is silence, never an error.
 */
import { mixer } from '../../audio/engine';
import type { StripHandle } from '../../audio/types';

mixer.declareStrip({ id: 'interp.neurons', label: 'Neurons', icon: '🔬' });

const MUTE_KEY = 'interp.neurons.muted';
const PENTATONIC = [0, 2, 4, 7, 9];

let strip: StripHandle | null = null;
let lastHover = 0;
let lastDose = 0;

function readMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === '1';
  } catch {
    return false;
  }
}

let muted = readMuted();

export function isMuted(): boolean {
  return muted;
}

export function setMuted(value: boolean): void {
  muted = value;
  try {
    localStorage.setItem(MUTE_KEY, value ? '1' : '0');
  } catch {
    // per-viewer convenience only
  }
}

function out(): { ac: AudioContext; node: AudioNode } | null {
  if (muted || document.hidden) return null;
  try {
    strip ??= mixer.connectStrip('interp.neurons');
    const ac = strip.context;
    if (ac.state === 'suspended') void ac.resume().catch(() => undefined);
    return ac.state === 'running' ? { ac, node: strip.input } : null;
  } catch {
    return null;
  }
}

function tone(freq: number, peak = 0.08, duration = 0.14, delay = 0): void {
  const o = out();
  if (!o) return;
  try {
    const t = o.ac.currentTime + delay;
    const osc = o.ac.createOscillator();
    const gain = o.ac.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(freq * 0.98, t + duration);
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(peak, t + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    osc.connect(gain).connect(o.node);
    osc.start(t);
    osc.stop(t + duration + 0.03);
    osc.onended = () => {
      osc.disconnect();
      gain.disconnect();
    };
  } catch {
    // sound is a garnish
  }
}

const note = (base: number, step: number) => base * 2 ** (PENTATONIC[step % 5] / 12);

export const neuronSound = {
  hover(layer: number, neuron: number): void {
    const now = performance.now();
    if (now - lastHover < 120) return;
    lastHover = now;
    tone(note(440, layer + neuron), 0.02, 0.09);
  },
  select(layer: number): void {
    const f = note(220, layer);
    tone(f, 0.1, 0.35);
    tone(f * 2, 0.05, 0.42, 0.045);
  },
  dose(dose: number): void {
    const now = performance.now();
    if (now - lastDose < 45) return;
    lastDose = now;
    tone(260 * 2 ** ((dose + 3) / 6), 0.05, 0.055);
  },
  layer(direction: number): void {
    tone(direction > 0 ? 330 : 262, 0.06, 0.16);
  },
  click(): void {
    tone(520, 0.04, 0.075);
  },
  reset(): void {
    tone(440, 0.06, 0.25);
    tone(220, 0.07, 0.35, 0.08);
  },
};
