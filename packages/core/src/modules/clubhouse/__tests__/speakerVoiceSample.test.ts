import { describe, expect, it } from 'vitest';

import { CLONE_SAMPLE_RATE, VoiceSampleCollector, sttWav } from '../speakerVoiceSample';

const RATE = CLONE_SAMPLE_RATE;
const ms = (n: number) => Math.round((RATE * n) / 1000);
const tone = (durMs: number, amp = 0.2) => {
  const out = new Float32Array(ms(durMs));
  for (let i = 0; i < out.length; i++) out[i] = amp * Math.sin((2 * Math.PI * 220 * i) / RATE);
  return out;
};
const silence = (durMs: number) => new Float32Array(ms(durMs));

/** Feed in the worklet's awkward block sizes, so the frame carry is exercised. */
const feed = (c: VoiceSampleCollector, audio: Float32Array) => {
  for (let i = 0; i < audio.length; i += 1003) c.push(audio.subarray(i, i + 1003));
};

describe('the speaker voice sample collector', () => {
  it('counts speech time and keeps nothing before anyone speaks', () => {
    const c = new VoiceSampleCollector();
    feed(c, silence(2000));
    expect(c.speechMs).toBe(0);
    expect(c.keptMs).toBe(0);
    feed(c, tone(1000));
    expect(c.speechMs).toBe(1000);
  });

  it('collapses a long pause to the hangover plus pre-roll', () => {
    const c = new VoiceSampleCollector(RATE, { hangoverMs: 300, prerollMs: 160 });
    feed(c, tone(1000));
    feed(c, silence(5000));
    feed(c, tone(1000));
    // 2 s of speech + 300 ms tail + 160 ms lead-in, not 7 s.
    expect(c.keptMs).toBeCloseTo(2460, -1);
  });

  it('is done once the speech target is reached, however long the silences', () => {
    const c = new VoiceSampleCollector(RATE, { targetSpeechMs: 3000 });
    for (let i = 0; i < 3; i++) {
      expect(c.done).toBe(false);
      feed(c, tone(1000));
      feed(c, silence(4000));
    }
    expect(c.done).toBe(true);
  });

  it('peak-normalises a quiet speaker, with the gain capped', () => {
    const loud = new VoiceSampleCollector();
    feed(loud, tone(500, 0.3));
    const peak = loud.audio().reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(peak).toBeCloseTo(0.89, 2);

    const faint = new VoiceSampleCollector(RATE, { threshold: 0.001 });
    feed(faint, tone(500, 0.01));
    const faintPeak = faint.audio().reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(faintPeak).toBeCloseTo(0.08, 2); // 8x, not up to 0.89
  });

  it('downsamples to the 16 kHz WAV the STT route reads', () => {
    const wav = new DataView(sttWav(tone(1000)));
    expect(wav.getUint32(24, true)).toBe(16000);
    expect(wav.getUint32(40, true)).toBe(16000 * 2); // one second of 16-bit mono
  });
});
