import { describe, expect, it } from 'vitest';

import { Downsampler, STT_SAMPLE_RATE, UtteranceBuffer, encodeWav } from '../liveCapture';

describe('the downsampler', () => {
  it('turns one second at 48 kHz into one second at 16 kHz', () => {
    const d = new Downsampler(48000);
    let total = 0;
    // In the worklet's 2048-frame posts, so the block boundaries are exercised.
    for (let i = 0; i < 48000; i += 2048) {
      total += d.process(new Float32Array(Math.min(2048, 48000 - i)).fill(0.25)).length;
    }
    expect(total).toBe(STT_SAMPLE_RATE);
  });

  it('does not drift at 44.1 kHz, a non-integer ratio', () => {
    const d = new Downsampler(44100);
    let total = 0;
    for (let s = 0; s < 10; s++) {
      for (let i = 0; i < 44100; i += 2048) {
        total += d.process(new Float32Array(Math.min(2048, 44100 - i))).length;
      }
    }
    // Ten seconds in, still within a sample of ten seconds out.
    expect(Math.abs(total - 10 * STT_SAMPLE_RATE)).toBeLessThanOrEqual(1);
  });

  it('keeps the level of what it averages', () => {
    const out = new Downsampler(48000).process(new Float32Array(4800).fill(0.5));
    expect(out.every((v) => Math.abs(v - 0.5) < 1e-6)).toBe(true);
  });
});

describe('the utterance buffer', () => {
  const ms = (n: number) => new Float32Array((STT_SAMPLE_RATE * n) / 1000);

  it('keeps only a short pre-roll while nobody is talking', () => {
    const buf = new UtteranceBuffer(STT_SAMPLE_RATE, 400);
    for (let i = 0; i < 50; i++) buf.push(ms(100));
    // Five seconds of room silence must not become the start of the next sentence.
    expect(buf.durationMs).toBeLessThanOrEqual(500);
    expect(buf.durationMs).toBeGreaterThanOrEqual(400);
  });

  it('starts an utterance with the pre-roll, so the first syllable is not clipped', () => {
    const buf = new UtteranceBuffer(STT_SAMPLE_RATE, 400);
    buf.push(ms(400).fill(0.1));
    buf.begin();
    buf.push(ms(1000).fill(0.9));
    const audio = buf.snapshot();
    expect(audio.length).toBe(ms(1400).length);
    expect(audio[0]).toBeCloseTo(0.1);
  });

  it('snapshots without ending, so live captions never interrupt the capture', () => {
    const buf = new UtteranceBuffer();
    buf.begin();
    buf.push(ms(500));
    expect(buf.snapshot().length).toBe(ms(500).length);
    buf.push(ms(500));
    expect(buf.snapshot().length).toBe(ms(1000).length);
    expect(buf.active).toBe(true);
  });

  it('hands back the whole utterance and returns to pre-roll on end', () => {
    const buf = new UtteranceBuffer();
    buf.begin();
    const first = buf.id;
    buf.push(ms(2000));
    expect(buf.end().length).toBe(ms(2000).length);
    expect(buf.active).toBe(false);
    expect(buf.durationMs).toBe(0);
    buf.begin();
    // A reply for the previous utterance can tell it is stale.
    expect(buf.id).not.toBe(first);
  });
});

describe('the WAV encoder', () => {
  it('writes the 16 kHz mono PCM the STT service reads without ffmpeg', () => {
    const wav = new DataView(encodeWav(new Float32Array([0, 1, -1, 0.5])));
    const tag = (o: number) => String.fromCharCode(...[0, 1, 2, 3].map((i) => wav.getUint8(o + i)));
    expect(tag(0)).toBe('RIFF');
    expect(tag(8)).toBe('WAVE');
    expect(wav.getUint16(22, true)).toBe(1); // mono
    expect(wav.getUint32(24, true)).toBe(16000);
    expect(wav.getUint16(34, true)).toBe(16); // bits
    expect(wav.getUint32(40, true)).toBe(8); // 4 samples * 2 bytes
    expect(wav.getInt16(46, true)).toBe(32767);
    expect(wav.getInt16(48, true)).toBe(-32768);
  });
});
