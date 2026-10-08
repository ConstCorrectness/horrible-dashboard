/// <reference types="@webgpu/types" />
/**
 * One GGUF model ready to chat: the runtime on the GPU, its tokenizer and chat
 * template, and the generation loop behind the worker's `generate` — templated
 * prompt in, streamed text and per-token distributions out.
 *
 * Kept apart from the worker so the loop can be tested in a page without one.
 */
import type { ChatMessage, StopReason, Usage } from '../protocol';
import { checkSupport } from './arch';
import { ggmlTypeName } from './ggml';
import { layoutProblems, readGgufHeader, type ByteSource, type GgufHeader } from './parse';
import { GgufRuntime, type LoadOptions } from './runtime';
import { DEFAULT_SAMPLER, sample } from './sample';
import { ChatTemplate } from './template';
import { Tokenizer } from './tokenizer';
import type { WebmlDevice } from '../gpu/device';

/** KV positions allocated when the caller does not say: room for an agent turn. */
export const DEFAULT_CONTEXT = 4096;

/** Prompt tokens submitted between checks for an interrupt. */
const PREFILL_BATCH = 32;

export interface SessionGenerate {
  messages: ChatMessage[];
  tools?: unknown[];
  maxNewTokens?: number;
  temperature?: number;
  /** Alternatives recorded per token for `onStep`; 0 = off. */
  topK?: number;
  templateKwargs?: Record<string, unknown>;
}

export interface SessionStep {
  index: number;
  token: string;
  p: number;
  entropy: number;
  topk: { token: string; p: number }[];
}

export interface SessionResult {
  text: string;
  stop: StopReason;
  usage: Usage;
  ttftMs: number;
  tokensPerSecond: number;
}

/**
 * What a header says before anything is downloaded: whether it can run here, and
 * if not, every reason. The tokenizer and template are checked too — a model
 * whose pre-tokenizer is unsupported is refused here, not after 2 GB.
 */
export function preflight(header: GgufHeader): { ok: true } | { ok: false; reasons: string[] } {
  const reasons: string[] = [...layoutProblems(header)];
  const support = checkSupport(header);
  if (!support.ok) reasons.push(...support.reasons);
  try {
    const tok = new Tokenizer(header.metadata);
    const t = header.metadata['tokenizer.chat_template'];
    new ChatTemplate(typeof t === 'string' ? t : undefined, tok);
  } catch (err) {
    reasons.push(err instanceof Error ? err.message : String(err));
  }
  return reasons.length ? { ok: false, reasons } : { ok: true };
}

/** The weight type holding most of the file's bytes: "Q8_0", "Q4_0", … */
export function dominantQuant(header: GgufHeader): string {
  const bytes = new Map<number, number>();
  for (const t of header.tensors) bytes.set(t.type, (bytes.get(t.type) ?? 0) + (t.bytes ?? 0));
  let best = 0;
  let most = -1;
  for (const [type, n] of bytes) if (n > most) [best, most] = [type, n];
  return ggmlTypeName(best);
}

export class GgufSession {
  private constructor(
    readonly runtime: GgufRuntime,
    readonly tokenizer: Tokenizer,
    readonly template: ChatTemplate,
    readonly quant: string,
    private readonly device: GPUDevice,
  ) {}

  static async open(
    gpu: WebmlDevice,
    source: ByteSource,
    options: LoadOptions = {},
  ): Promise<GgufSession> {
    const header = await readGgufHeader(source);
    const check = preflight(header);
    if (!check.ok) throw new Error(`cannot run this model: ${check.reasons.join('; ')}`);
    const tokenizer = new Tokenizer(header.metadata);
    const t = header.metadata['tokenizer.chat_template'];
    const template = new ChatTemplate(typeof t === 'string' ? t : undefined, tokenizer);
    const runtime = await GgufRuntime.load(gpu, header, source, {
      ...options,
      contextLength: options.contextLength ?? DEFAULT_CONTEXT,
    });
    return new GgufSession(runtime, tokenizer, template, dominantQuant(header), gpu.device);
  }

  destroy(): void {
    this.runtime.destroy();
  }

  async generate(
    req: SessionGenerate,
    handlers: { onDelta?: (text: string) => void; onStep?: (step: SessionStep) => void },
    interrupted: () => boolean,
    random: () => number = Math.random,
  ): Promise<SessionResult> {
    const started = performance.now();
    const prompt = this.template.encode(req.messages, {
      tools: req.tools,
      kwargs: req.templateKwargs,
    });
    const context = this.runtime.context;
    if (!prompt.length) throw new Error('the chat template rendered an empty prompt');
    if (prompt.length >= context) {
      throw new Error(
        `the prompt is ${prompt.length} tokens; this model is loaded with a ${context}-token context`,
      );
    }
    const maxNew = Math.min(req.maxNewTokens ?? 1024, context - prompt.length);
    const usage = (completionTokens: number): Usage => ({
      promptTokens: prompt.length,
      completionTokens,
    });

    // Prefill in batches, so Stop is heard during a long prompt too.
    for (let at = 0; at < prompt.length - 1; at += PREFILL_BATCH) {
      if (interrupted()) {
        return { text: '', stop: 'interrupt', usage: usage(0), ttftMs: 0, tokensPerSecond: 0 };
      }
      const end = Math.min(at + PREFILL_BATCH, prompt.length - 1);
      for (let i = at; i < end; i++) this.runtime.queueStep(prompt[i], i);
      await this.device.queue.onSubmittedWorkDone();
    }
    let logits = await this.runtime.forward([prompt[prompt.length - 1]], prompt.length - 1);

    const sampler = {
      ...DEFAULT_SAMPLER,
      temperature: req.temperature ?? DEFAULT_SAMPLER.temperature,
    };
    const record = Math.max(0, Math.min(20, req.topK ?? 0));
    const decoder = new TextDecoder();
    const generated: number[] = [];
    let text = '';
    let stop: StopReason = 'length';
    let firstAt = 0;

    for (let i = 0; i < maxNew; i++) {
      if (interrupted()) {
        stop = 'interrupt';
        break;
      }
      const { id, dist } = sample(logits, sampler, random, record);
      if (!firstAt) firstAt = performance.now();
      if (this.tokenizer.eog.has(id)) {
        stop = 'eos';
        break;
      }
      generated.push(id);
      const delta = decoder.decode(this.tokenizer.piece(id), { stream: true });
      if (delta) {
        text += delta;
        handlers.onDelta?.(delta);
      }
      if (dist && handlers.onStep) {
        handlers.onStep({
          index: generated.length - 1,
          token: this.tokenizer.decode([id], true),
          p: dist.probs[id] ?? 0,
          entropy: dist.entropy,
          topk: dist.top.map((alt) => ({ token: this.tokenizer.decode([alt.id], true), p: alt.p })),
        });
      }
      if (i === maxNew - 1) break;
      logits = await this.runtime.forward([id], prompt.length + i);
    }
    const tail = decoder.decode();
    if (tail) {
      text += tail;
      handlers.onDelta?.(tail);
    }

    const ended = performance.now();
    const decodeSeconds = firstAt ? (ended - firstAt) / 1000 : 0;
    return {
      text,
      stop,
      usage: usage(generated.length),
      ttftMs: firstAt ? Math.round(firstAt - started) : 0,
      tokensPerSecond:
        generated.length > 1 && decodeSeconds > 0 ? (generated.length - 1) / decodeSeconds : 0,
    };
  }
}
