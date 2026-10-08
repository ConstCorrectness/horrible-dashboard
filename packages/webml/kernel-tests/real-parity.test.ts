/**
 * 6.1's exit check on a real model: greedy decoding through the WGSL engine must
 * pick the same tokens as llama.cpp, with each step's top-5 probabilities within
 * 0.01. Opt-in, because it needs the model file and a reference run:
 *
 *   uv run --with llama-cpp-python python scripts/gen_webml_llama_parity.py \
 *     --model ~/models/SmolLM2-360M-Instruct-Q8_0.gguf --out /tmp/smollm2.json
 *   WEBML_PARITY_MODEL=~/models/SmolLM2-360M-Instruct-Q8_0.gguf \
 *   WEBML_PARITY_EXPECTED=/tmp/smollm2.json \
 *     pnpm --filter @horrible/webml test:kernels
 *
 * Both files are served to the browser through Vite's `/@fs/` (vitest.config.ts
 * allows their directories). On SwiftShader a 360M model takes minutes; on a real
 * GPU, run the same command without the SwiftShader flag.
 */
import { describe, expect, inject, it } from 'vitest';

import { distribution } from '../src/distribution';
import { readGgufHeader } from '../src/gguf/parse';
import { GgufRuntime } from '../src/gguf/runtime';
import { bytesSource } from '../src/gguf/source';
import { gpu } from './harness';

declare module 'vitest' {
  interface ProvidedContext {
    parityModel: string;
    parityExpected: string;
  }
}

interface Reference {
  model: string;
  prompt: number[];
  steps: { token: number; top: [id: number, p: number][] }[];
}

const modelPath = inject('parityModel');
const expectedPath = inject('parityExpected');

describe.skipIf(!modelPath || !expectedPath)('a real GGUF vs llama.cpp', () => {
  it(
    'same greedy tokens, top-5 probabilities within 0.01',
    async () => {
      const ref: Reference = await (await fetch(`/@fs${expectedPath}`)).json();
      const bytes = new Uint8Array(await (await fetch(`/@fs${modelPath}`)).arrayBuffer());
      const source = bytesSource(bytes);
      const rt = await GgufRuntime.load(await gpu(), await readGgufHeader(source), source, {
        contextLength: ref.prompt.length + ref.steps.length + 1,
      });
      try {
        let logits = await rt.forward(ref.prompt);
        let worst = 0;
        for (let i = 0; i < ref.steps.length; i++) {
          const want = ref.steps[i];
          const dist = distribution(logits, 5);
          const token = dist.top[0].id;
          expect(
            token,
            `step ${i}: picked ${token}, llama.cpp picked ${want.token} (its top-5: ${JSON.stringify(want.top)}; ours: ${JSON.stringify(dist.top)})`,
          ).toBe(want.token);
          for (const [id, p] of want.top) worst = Math.max(worst, Math.abs(dist.probs[id] - p));
          logits = await rt.forward([token], ref.prompt.length + i);
        }
        expect(worst, 'largest top-5 probability difference').toBeLessThan(0.01);
      } finally {
        rt.destroy();
      }
    },
    60 * 60 * 1000,
  );
});
