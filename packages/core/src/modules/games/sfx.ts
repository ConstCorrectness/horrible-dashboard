/**
 * Tiny WebAudio blips for the arcade feel — no assets, just oscillators. Gated on
 * the `games.sound` setting (off by default) and fully best-effort: any audio
 * failure is swallowed.
 */
import { getSetting } from '../../settings';

let ctx: AudioContext | null = null;

function context(): AudioContext | null {
  try {
    ctx ??= new AudioContext();
    return ctx;
  } catch {
    return null;
  }
}

function blip(freqs: number[], duration = 0.09, gainPeak = 0.04): void {
  if (getSetting<boolean>('games.sound') !== true) return;
  const ac = context();
  if (!ac) return;
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
      osc.connect(gain).connect(ac.destination);
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
