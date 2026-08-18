/**
 * Tiny WebAudio blips for the arcade feel — no assets, just oscillators. Gated on
 * the `games.sound` setting (off by default) and fully best-effort: any audio
 * failure is swallowed.
 */
import { mixer } from '../audio/engine';
import type { StripHandle } from '../audio/types';
import { getSetting } from '../../settings';

mixer.declareStrip({ id: 'games', label: 'Games', icon: '🎮' });

let ctx: AudioContext | null = null;
let strip: StripHandle | null = null;

/**
 * The mixer's context and this module's fader.
 *
 * Was `new AudioContext()`. A private context cannot be routed and cannot even
 * be connected to nodes from anywhere else — so these blips were the one sound
 * in the app that no volume control could reach.
 */
function context(): AudioContext | null {
  try {
    if (!strip) {
      strip = mixer.connectStrip('games');
      ctx = strip.context;
    }
    return ctx;
  } catch {
    return null;
  }
}

/** Where a blip connects. Null when audio is unavailable. */
function output(): AudioNode | null {
  return context() ? (strip?.input ?? null) : null;
}

function blip(freqs: number[], duration = 0.09, gainPeak = 0.04): void {
  if (getSetting<boolean>('games.sound') !== true) return;
  const ac = context();
  const out = output();
  if (!ac || !out) return;
  try {
    const now = ac.currentTime;
    freqs.forEach((freq, i) => {
      const osc = ac.createOscillator();
      const gain = ac.createGain();
      osc.type = 'square';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * duration);
      gain.gain.linearRampToValueAtTime(gainPeak, now + i * duration + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + (i + 1) * duration);
      osc.connect(gain).connect(out);
      osc.start(now + i * duration);
      osc.stop(now + (i + 1) * duration + 0.02);
    });
  } catch {
    // sound is a garnish, never an error
  }
}

export const sfx = {
  matchStart: () => blip([392, 523, 659], 0.1),
  move: () => blip([880], 0.05, 0.02),
  win: () => blip([523, 659, 784, 1047], 0.11),
  lose: () => blip([330, 262, 196], 0.13),
  toast: () => blip([660, 880], 0.07, 0.03),
};
