/**
 * The generation loop end to end on the tiny model: chat template → tokenizer →
 * runtime → sampler → streamed text. Greedy decoding must reproduce llama.cpp's
 * continuation (the same reference as forward.test.ts), now starting from text.
 */
import { describe, expect, it } from 'vitest';

import { GgufSession, type SessionStep } from '../src/gguf/session';
import { bytesSource } from '../src/gguf/source';
import f32Expected from './fixtures/tiny-llama-f32.expected.json';
import f32Url from './fixtures/tiny-llama-f32.gguf?url';
import { fixtureBytes, gpu } from './harness';

/** The fixture's tokens t2…t63 are user-defined, so a prompt is their texts run together. */
const text = (ids: number[]) => ids.map((i) => `t${i}`).join('');

async function open(): Promise<GgufSession> {
  return GgufSession.open(await gpu(), bytesSource(await fixtureBytes(f32Url)));
}

describe('GgufSession', () => {
  it('greedy from a templated text prompt continues exactly as llama.cpp does', async () => {
    const s = await open();
    try {
      expect(s.template.encode([{ role: 'user', content: text(f32Expected.prompt) }])).toEqual(
        f32Expected.prompt,
      );
      const deltas: string[] = [];
      const result = await s.generate(
        {
          messages: [{ role: 'user', content: text(f32Expected.prompt) }],
          temperature: 0,
          maxNewTokens: f32Expected.generated.length,
        },
        { onDelta: (d) => deltas.push(d) },
        () => false,
      );
      expect(result.text).toBe(text(f32Expected.generated));
      expect(deltas.join('')).toBe(result.text);
      expect(result.stop).toBe('length');
      expect(result.usage).toEqual({
        promptTokens: f32Expected.prompt.length,
        completionTokens: f32Expected.generated.length,
        cachedTokens: 0,
      });
    } finally {
      s.destroy();
    }
  });

  it('reports each token with the distribution it was drawn from', async () => {
    const s = await open();
    try {
      const steps: { index: number; token: string; p: number; topk: { token: string }[] }[] = [];
      await s.generate(
        {
          messages: [{ role: 'user', content: text(f32Expected.prompt) }],
          temperature: 0,
          maxNewTokens: 4,
          topK: 3,
        },
        { onStep: (step) => steps.push(step) },
        () => false,
      );
      expect(steps.map((st) => st.index)).toEqual([0, 1, 2, 3]);
      expect(steps.map((st) => st.token)).toEqual(
        f32Expected.generated.slice(0, 4).map((i) => `t${i}`),
      );
      for (const st of steps) {
        expect(st.topk).toHaveLength(3);
        // Greedy picks the most likely token.
        expect(st.topk[0].token).toBe(st.token);
      }
    } finally {
      s.destroy();
    }
  });

  it('reads out the logit lens at each token when asked, and only then', async () => {
    const s = await open();
    try {
      const run = async (lens: boolean) => {
        const steps: SessionStep[] = [];
        const result = await s.generate(
          {
            messages: [{ role: 'user', content: text(f32Expected.prompt) }],
            temperature: 0,
            maxNewTokens: 4,
            lens,
          },
          { onStep: (step) => steps.push(step) },
          () => false,
        );
        return { steps, result };
      };
      const { steps, result } = await run(true);
      // The lens alone turns the steps on; no alternatives were asked for.
      expect(steps).toHaveLength(4);
      for (const st of steps) {
        expect(st.topk).toEqual([]);
        expect(st.layers).toHaveLength(s.runtime.config.layers);
        // Greedy: the last layer's best token is the token chosen.
        expect(st.layers!.at(-1)!.top[0].token).toBe(st.token);
      }
      // The lens changes what is read out, not what is generated.
      expect(result.text).toBe(text(f32Expected.generated.slice(0, 4)));
      expect(s.runtime.lens).toBe(false);
      expect((await run(false)).steps).toEqual([]);
    } finally {
      s.destroy();
    }
  });

  it('reuses the KV cache for the prefix a new prompt shares with the last reply', async () => {
    const s = await open();
    const { prompt, generated } = f32Expected;
    const greedy = (ids: number[], maxNewTokens: number) =>
      s.generate(
        { messages: [{ role: 'user', content: text(ids) }], temperature: 0, maxNewTokens },
        {},
        () => false,
      );
    try {
      // Round 1: 8 prompt tokens, 4 replies. The cache then holds the prompt and
      // the first 3 replies (the 4th was sampled, never fed).
      const first = await greedy(prompt, 4);
      expect(first.text).toBe(text(generated.slice(0, 4)));
      expect(first.usage.cachedTokens).toBe(0);

      // Round 2 extends that conversation: 11 tokens are already cached, so only 3
      // are prefilled — and the continuation is still llama.cpp's.
      const second = await greedy([...prompt, ...generated.slice(0, 6)], 5);
      expect(second.usage.cachedTokens).toBe(prompt.length + 3);
      expect(second.text).toBe(text(generated.slice(6, 11)));

      // A prompt that differs from the first token on reuses nothing, and is right.
      const other = [generated[0], ...prompt.slice(1)];
      const third = await greedy(other, 3);
      expect(third.usage.cachedTokens).toBe(0);
      const fresh = await open();
      try {
        const want = await fresh.generate(
          { messages: [{ role: 'user', content: text(other) }], temperature: 0, maxNewTokens: 3 },
          {},
          () => false,
        );
        expect(third.text).toBe(want.text);
      } finally {
        fresh.destroy();
      }

      // The same prompt again: everything but its last token is reused.
      const again = await greedy(other, 3);
      expect(again.usage.cachedTokens).toBe(other.length - 1);
      expect(again.text).toBe(third.text);
    } finally {
      s.destroy();
    }
  });

  it('stops when interrupted, and refuses a prompt longer than the context', async () => {
    const s = await open();
    try {
      let calls = 0;
      const result = await s.generate(
        {
          messages: [{ role: 'user', content: text(f32Expected.prompt) }],
          temperature: 0.8,
          maxNewTokens: 30,
        },
        {},
        () => ++calls > 12,
      );
      expect(result.stop).toBe('interrupt');
      expect(result.usage.completionTokens).toBeLessThan(30);

      // The tiny model's context is 64 positions.
      await expect(
        s.generate(
          { messages: [{ role: 'user', content: text(new Array(70).fill(5)) }] },
          {},
          () => false,
        ),
      ).rejects.toThrow(/the prompt is 70 tokens; this model is loaded with a 64-token context/);
    } finally {
      s.destroy();
    }
  });
});
