/**
 * Run a comparison: one prompt, two GGUF models, the same reply text.
 *
 * A writes the reply (greedy, so the text is the base model's own), then each
 * model is made to *read* that text with the lens on (`forced`), so every
 * position is compared on identical tokens: what each predicted there, and at
 * which layer. The window holds one model at a time, so this loads A, then B —
 * and leaves B loaded.
 */
import type { ChatMessage, StepEvent } from '@horrible/webml';

import { webmlEngine } from '../engine';
import type { ForcedRun } from './model';

export type ComparePhase =
  | { kind: 'loading'; model: string }
  | { kind: 'writing'; model: string }
  | { kind: 'reading'; model: string; done: number; total: number };

export interface CompareRequest {
  a: string;
  b: string;
  prompt: string;
  maxTokens: number;
  signal?: AbortSignal;
  onPhase?: (phase: ComparePhase) => void;
}

export interface CompareResult {
  text: string;
  a: ForcedRun;
  b: ForcedRun;
}

async function read(
  model: string,
  messages: ChatMessage[],
  text: string,
  total: number,
  req: CompareRequest,
): Promise<ForcedRun> {
  const engine = webmlEngine();
  req.onPhase?.({ kind: 'loading', model });
  await engine.load(model, 'q4f16', 'webgpu');
  const steps: StepEvent[] = [];
  req.onPhase?.({ kind: 'reading', model, done: 0, total });
  await engine.generate(
    { messages, forced: text, lens: true, topK: 5 },
    {
      signal: req.signal,
      onStep: (step) => {
        steps.push(step);
        req.onPhase?.({ kind: 'reading', model, done: steps.length, total });
      },
    },
  );
  return {
    model,
    steps: steps.map((s) => ({ token: s.token, p: s.p, entropy: s.entropy, layers: s.layers })),
  };
}

export async function runComparison(req: CompareRequest): Promise<CompareResult> {
  const engine = webmlEngine();
  const messages: ChatMessage[] = [{ role: 'user', content: req.prompt }];
  req.onPhase?.({ kind: 'loading', model: req.a });
  await engine.load(req.a, 'q4f16', 'webgpu');
  req.onPhase?.({ kind: 'writing', model: req.a });
  // The reply as its tokens spelled it, specials included: the reply's text drops a
  // control token the model emitted mid-reply, and reading the text back would
  // then shift every position after it by one.
  const spelled: string[] = [];
  const written = await engine.generate(
    { messages, temperature: 0, maxNewTokens: req.maxTokens, topK: 1 },
    {
      signal: req.signal,
      onStep: (step) => {
        spelled.push(step.token);
        req.onPhase?.({ kind: 'writing', model: req.a });
      },
    },
  );
  const forced = spelled.join('');
  if (!forced) throw new Error(`${req.a} answered with nothing to compare`);
  if (req.signal?.aborted) throw new Error('stopped');
  const a = await read(req.a, messages, forced, spelled.length, req);
  if (req.signal?.aborted) throw new Error('stopped');
  const b = await read(req.b, messages, forced, spelled.length, req);
  return { text: written.text, a, b };
}
