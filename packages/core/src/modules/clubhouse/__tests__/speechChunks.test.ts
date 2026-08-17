import { describe, expect, it } from 'vitest';

import { splitForSpeech } from '../speechChunks';

describe('splitForSpeech', () => {
  it('splits on sentence boundaries once past the first-chunk budget', () => {
    /** A reply short enough to fit the opening budget is deliberately *not* split —
     *  the extra request would cost more than it saves. */
    const short = 'One thing here. Another thing there.';
    expect(splitForSpeech(short)).toEqual([short]);

    const first =
      'One thing here that runs on for a good while so that it comfortably fills the ' +
      'whole opening budget on its own.';
    const text = `${first} Another thing there. A third one.`;
    const chunks = splitForSpeech(text);
    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks[0]).toBe(first);
    expect(chunks.join(' ')).toBe(text);
  });

  it('keeps the first chunk short, because it is the only latency anyone feels', () => {
    const long =
      'Ada, it is just the three of us up here, you and Grace and me, so nobody else ' +
      'has raised a hand yet. We are digging into compilers and type systems today. ' +
      'If you want, we can let Linus in next and hear what he has been working on.';
    const chunks = splitForSpeech(long);
    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks[0].length).toBeLessThanOrEqual(120);
    // Nothing is lost or reordered — the room hears the whole reply.
    expect(chunks.join(' ')).toBe(long);
  });

  it('never drops or reorders text', () => {
    const text = 'First! Second? Third... and a trailing clause';
    expect(splitForSpeech(text).join(' ')).toBe(text);
  });

  it('returns a single chunk for a short reply', () => {
    expect(splitForSpeech('Sure, happy to help.')).toEqual(['Sure, happy to help.']);
  });

  it('merges a fragment too short to be worth its own request', () => {
    /** "Right." alone is a full round trip for one syllable, and the gap it leaves
     *  sounds like hesitation. */
    const chunks = splitForSpeech('Right. That is a good point about type inference.');
    expect(chunks).toHaveLength(1);
  });

  it('breaks an over-long sentence on a clause boundary', () => {
    const runOn = `We could talk about ${'type inference, '.repeat(30)}and then stop.`;
    const chunks = splitForSpeech(runOn);
    expect(chunks.length).toBeGreaterThan(1);
    for (const c of chunks) expect(c.length).toBeLessThanOrEqual(241);
    expect(chunks.join(' ')).toBe(runOn);
  });

  it('handles empty and whitespace input', () => {
    expect(splitForSpeech('')).toEqual([]);
    expect(splitForSpeech('   ')).toEqual([]);
  });

  it('keeps a closing quote with its sentence', () => {
    const chunks = splitForSpeech('She said "hello there everyone." Then she left the stage.');
    expect(chunks[0]).toContain('."');
  });
});
