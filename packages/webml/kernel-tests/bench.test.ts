/**
 * 6.4's speed measurements on a real model, written to
 * `kernel-tests/.results/bench-<tag>-<model>.json`. Opt-in, and only meaningful
 * on a real GPU:
 *
 *   WEBML_GPU=hardware WEBML_BENCH_MODEL=~/models/SmolLM2-360M-Instruct-Q4_K_M.gguf \
 *   WEBML_BENCH_TAG=baseline \
 *     pnpm --filter @horrible/webml exec vitest run --project kernels bench
 *
 * What it measures, through `GgufSession.generate` as the worker runs it:
 *
 * - **chat**: a short user prompt, 128 greedy tokens. TTFT and decode tok/s — the
 *   plan's ≤ 0.7 s and > 27 tok/s (SmolLM2-360M Q4_K_M) targets.
 * - **chatLens**: the same chat with the logit lens read out at every token (6.6),
 *   steps decoded as the playground would: what the lens costs per token.
 * - **agent**: two rounds over one long system prompt (`WEBML_BENCH_SYSTEM`
 *   tokens, default 3500, like an agent's tools and instructions), 32 tokens
 *   each. Round 2 repeats round 1's messages plus a reply and a new question, so
 *   with prefix reuse it prefills only the difference — the < 10 s target.
 */
import { describe, expect, inject, it } from 'vitest';

import type { ChatMessage } from '../src/protocol';
import { GgufSession } from '../src/gguf/session';
import { bytesSource } from '../src/gguf/source';
import { fetchLocal, gpu, report } from './harness';

declare module 'vitest' {
  interface ProvidedContext {
    benchModel: string;
    benchTag: string;
    benchSystem: number;
  }
}

const modelPath = inject('benchModel');

/** Deterministic instructions, grown sentence by sentence until `tokens` long. */
function systemPrompt(session: GgufSession, tokens: number): string {
  const topics = ['billing', 'travel', 'weather', 'sports', 'cooking', 'music', 'health'];
  let text = 'You are a careful assistant. Follow every rule below exactly.\n';
  for (let i = 1; ; i++) {
    text +=
      `Rule ${i}: when the user asks about ${topics[i % topics.length]} item ${(i * 37) % 1000}, ` +
      `answer in at most ${2 + (i % 5)} sentences and cite section ${(i * 7) % 13}.${i % 4 ? ' ' : '\n'}`;
    if (i % 20 === 0 && session.tokenizer.encode(text).length >= tokens) return text;
  }
}

describe.skipIf(!modelPath)('speed on a real GGUF', () => {
  it(
    'chat TTFT and decode, then two agent rounds over a long prompt',
    async () => {
      const name = modelPath.replace(/\\/g, '/').split('/').pop()!;
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
      const device = await gpu();

      const loadStart = performance.now();
      const bytes = new Uint8Array(await (await fetchLocal(modelPath)).arrayBuffer());
      const read = performance.now() - loadStart;
      const session = await GgufSession.open(device, bytesSource(bytes));
      const loadMs = performance.now() - loadStart - read;

      const kwargs = session.thinking ? { enable_thinking: false } : undefined;
      const run = async (messages: ChatMessage[], maxNewTokens: number, lens = false) => {
        const started = performance.now();
        const result = await session.generate(
          { messages, maxNewTokens, temperature: 0, templateKwargs: kwargs, lens },
          lens ? { onStep: () => undefined } : {},
          () => false,
        );
        return { ...result, wallMs: Math.round(performance.now() - started) };
      };

      try {
        // Compile and touch everything once; not measured.
        await run([{ role: 'user', content: 'Hi' }], 4);

        const poem: ChatMessage[] = [
          { role: 'user', content: 'Write a short poem about the sea.' },
        ];
        const chat = await run(poem, 128);
        const chatLens = await run(poem, 128, true);

        const system = systemPrompt(session, inject('benchSystem'));
        const round1Messages: ChatMessage[] = [
          { role: 'system', content: system },
          { role: 'user', content: 'Which rule covers weather item 259?' },
        ];
        const round1 = await run(round1Messages, 32);
        const round2 = await run(
          [
            ...round1Messages,
            { role: 'assistant', content: round1.text },
            { role: 'user', content: 'And which one covers music item 222?' },
          ],
          32,
        );

        // Per-kernel GPU time, last: it overwrites cache entries the session holds.
        const depth = round2.usage.promptTokens + round2.usage.completionTokens;
        const profile = device.timestamps
          ? {
              decodeAtPosition: depth,
              decode: await session.runtime.profile('decode', depth),
              decodeLens: await (async () => {
                session.runtime.lens = true;
                try {
                  return await session.runtime.profile('decode', depth);
                } finally {
                  session.runtime.lens = false;
                }
              })(),
              prefill: await session.runtime.profile('prefill', 0, session.runtime.batch),
            }
          : null;

        const prefill = (r: typeof round1) =>
          r.ttftMs ? Math.round(r.usage.promptTokens / (r.ttftMs / 1000)) : 0;
        await report(`bench-${inject('benchTag')}-${name}`, {
          model: name,
          quant: session.quant,
          adapter: adapter && {
            vendor: adapter.info.vendor,
            architecture: adapter.info.architecture,
            description: adapter.info.description,
          },
          features: { f16: device.f16, subgroups: device.subgroups, timestamps: device.timestamps },
          loadMs: Math.round(loadMs),
          chat: {
            promptTokens: chat.usage.promptTokens,
            tokens: chat.usage.completionTokens,
            ttftMs: chat.ttftMs,
            decodeTokensPerSecond: +chat.tokensPerSecond.toFixed(1),
          },
          chatLens: {
            tokens: chatLens.usage.completionTokens,
            decodeTokensPerSecond: +chatLens.tokensPerSecond.toFixed(1),
          },
          agent: [round1, round2].map((r) => ({
            promptTokens: r.usage.promptTokens,
            tokens: r.usage.completionTokens,
            ttftMs: r.ttftMs,
            prefillTokensPerSecond: prefill(r),
            decodeTokensPerSecond: +r.tokensPerSecond.toFixed(1),
            cachedTokens: r.usage.cachedTokens ?? 0,
            wallMs: r.wallMs,
          })),
          batch: session.runtime.batch,
          profile,
        });
        expect(chat.usage.completionTokens).toBeGreaterThan(0);
      } finally {
        session.destroy();
      }
    },
    2 * 60 * 60 * 1000,
  );
});
