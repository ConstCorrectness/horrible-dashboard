/**
 * 6.1's exit check on a real model: greedy decoding through the WGSL engine must
 * pick the same tokens as llama.cpp, with each step's top-5 probabilities within
 * 0.01. Opt-in, because it needs the model file and a reference run:
 *
 *   uv run --with llama-cpp-python python scripts/gen_webml_llama_parity.py \
 *     --f32 --model ~/models/SmolLM2-360M-Instruct-Q8_0.gguf --out ~/models/smollm2.json
 *   WEBML_GPU=hardware WEBML_PARITY_MODEL=~/models/SmolLM2-360M-Instruct-Q8_0.gguf \
 *   WEBML_PARITY_EXPECTED=~/models/smollm2.json \
 *     pnpm --filter @horrible/webml exec vitest run --project kernels real-parity
 *
 * `--f32` matters: without it llama.cpp rounds activations to q8 inside quantized
 * matmuls, which alone moves probabilities by up to ~0.08 on Qwen3-0.6B Q8_0 and
 * flips near-ties (see the script's docstring).
 *
 * Both files are served to the browser through Vite's `/@fs/` (vitest.config.ts
 * allows their directories). On Windows that only works on the project's drive —
 * Vite drops the drive letter — so reach files on another drive through a junction.
 * On SwiftShader a 360M model takes minutes; `WEBML_GPU=hardware` uses the real GPU.
 *
 * References recorded from prompt text also carry that text and a corpus of
 * awkward strings with llama.cpp's ids for each, and the engine's tokenizer must
 * reproduce them on the model's own vocabulary.
 */
import { describe, expect, inject, it } from 'vitest';

import { distribution } from '../src/distribution';
import { readGgufHeader } from '../src/gguf/parse';
import { GgufRuntime } from '../src/gguf/runtime';
import { bytesSource } from '../src/gguf/source';
import { Tokenizer } from '../src/gguf/tokenizer';
import { fetchLocal, gpu, report } from './harness';

declare module 'vitest' {
  interface ProvidedContext {
    parityModel: string;
    parityExpected: string;
  }
}

interface Reference {
  model: string;
  /** The prompt's text, when its ids were tokenized from it (BOS, special tokens). */
  text?: string | null;
  prompt: number[];
  steps: { token: number; top: [id: number, p: number][] }[];
  /** llama.cpp's ids for each string, without BOS or special-token parsing. */
  corpus?: { text: string; ids: number[] }[];
}

const modelPath = inject('parityModel');
const expectedPath = inject('parityExpected');

describe.skipIf(!modelPath || !expectedPath)('a real GGUF vs llama.cpp', () => {
  it(
    'same greedy tokens, top-5 probabilities within 0.01',
    async () => {
      const ref: Reference = await (await fetchLocal(expectedPath)).json();
      const bytes = new Uint8Array(await (await fetchLocal(modelPath)).arrayBuffer());
      const source = bytesSource(bytes);
      const header = await readGgufHeader(source);

      const tokenizer = new Tokenizer(header.metadata);
      if (ref.text) {
        expect(tokenizer.encode(ref.text, { addBos: true }), 'the prompt').toEqual(ref.prompt);
      }
      const wrong = (ref.corpus ?? [])
        .map(({ text, ids }) => ({
          text,
          ids,
          got: tokenizer.encode(text, { parseSpecial: false }),
        }))
        .filter(({ ids, got }) => ids.join(' ') !== got.join(' '))
        .map(({ text, ids, got }) => `${JSON.stringify(text)}: llama.cpp ${ids} ours ${got}`);
      expect(wrong, wrong.join('\n')).toEqual([]);

      const rt = await GgufRuntime.load(await gpu(), header, source, {
        contextLength: ref.prompt.length + ref.steps.length + 1,
      });
      try {
        let logits = await rt.forward(ref.prompt);
        const diffs: { step: number; diff: number; ours: string; theirs: string }[] = [];
        for (let i = 0; i < ref.steps.length; i++) {
          const want = ref.steps[i];
          const dist = distribution(logits, 5);
          const token = dist.top[0].id;
          const ours = JSON.stringify(dist.top.map((t) => [t.id, +t.p.toFixed(4)]));
          const theirs = JSON.stringify(want.top.map(([id, p]) => [id, +p.toFixed(4)]));
          expect(
            token,
            `step ${i}: picked ${token}, llama.cpp picked ${want.token} (its top-5: ${theirs}; ours: ${ours})`,
          ).toBe(want.token);
          let diff = 0;
          for (const [id, p] of want.top) diff = Math.max(diff, Math.abs(dist.probs[id] - p));
          diffs.push({ step: i, diff, ours, theirs });
          logits = await rt.forward([token], ref.prompt.length + i);
        }
        const worst = [...diffs].sort((a, b) => b.diff - a.diff).slice(0, 3);
        const mean = diffs.reduce((s, d) => s + d.diff, 0) / diffs.length;
        await report(`parity-${ref.model}`, {
          model: ref.model,
          kvCache: rt.kvCache,
          tokens: diffs.length,
          worst: worst[0].diff,
          mean,
        });
        expect(
          worst[0].diff,
          `largest top-5 probability difference (mean ${mean.toFixed(4)}); worst steps: ` +
            worst
              .map((d) => `#${d.step} ${d.diff.toFixed(4)} ours ${d.ours} llama.cpp ${d.theirs}`)
              .join(' | '),
        ).toBeLessThan(0.01);
      } finally {
        rt.destroy();
      }
    },
    60 * 60 * 1000,
  );
});
