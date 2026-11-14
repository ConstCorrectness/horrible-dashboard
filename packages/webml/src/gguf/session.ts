/// <reference types="@webgpu/types" />
/**
 * One GGUF model ready to chat: the runtime on the GPU, its tokenizer and chat
 * template, and the generation loop behind the worker's `generate` — templated
 * prompt in, streamed text and per-token distributions out.
 *
 * Kept apart from the worker so the loop can be tested in a page without one.
 */
import type { ChatMessage, LayerLens, StopReason, Usage } from '../protocol';
import { checkSupport } from './arch';
import { ggmlTypeName } from './ggml';
import { layoutProblems, readGgufHeader, type ByteSource, type GgufHeader } from './parse';
import { GgufRuntime, type LoadOptions, type NeuronEdit } from './runtime';
import { distribution } from '../distribution';
import { DEFAULT_SAMPLER } from './sample';
import { ChatTemplate } from './template';
import { Tokenizer } from './tokenizer';
import type { WebmlDevice } from '../gpu/device';

/** KV positions allocated when the caller does not say: room for an agent turn. */
export const DEFAULT_CONTEXT = 4096;

export interface SessionGenerate {
  messages: ChatMessage[];
  tools?: unknown[];
  maxNewTokens?: number;
  temperature?: number;
  /** Alternatives recorded per token for `onStep`; 0 = off. */
  topK?: number;
  /** Read out the logit lens at each generated token, into `onStep`. */
  lens?: boolean;
  templateKwargs?: Record<string, unknown>;
  /**
   * Don't sample: feed this text as the reply, a token at a time, and report at
   * each position what the model predicted there (`onStep`, temperature 1) — the
   * forced token's probability, the alternatives and, with `lens`, every layer.
   * Two models given the same text are then compared position by position.
   */
  forced?: string;
  /**
   * Neuron edits on the feed-forward activations, for this reply's every pass
   * (prompt included). Absent or empty: the unedited model. Changing them from one
   * call to the next drops the KV cache, so no position computed under one set of
   * edits is reused under another.
   */
  neuronEdits?: NeuronEdit[];
}

/** One position's feed-forward activations, from `captureActivations`. */
export interface SessionCapture {
  /** layers × ffn, row-major: each layer's activation, after any neuron edits. */
  activations: Float32Array;
  layers: number;
  ffn: number;
  /** The absolute position captured. */
  position: number;
  /** The token read at that position, and the one predicted from it (the reply's). */
  input: string;
  predicted: string;
}

/**
 * A neutral passage the residual stream's typical size is measured on, once per
 * load: plain descriptive prose, so no layer's median is pulled by an odd topic.
 */
export const REFERENCE_PASSAGE =
  'The library on the corner opens at nine in the morning and closes at six. ' +
  'Inside, long tables stand under tall windows, and the shelves run from the ' +
  'floor almost to the ceiling. Students come in the afternoon to read and ' +
  'write, while older visitors prefer the quiet hours before lunch. A small ' +
  'garden behind the building has benches, a few trees and a fountain that ' +
  'runs from spring until the first cold weeks of autumn.';

/** The median of a non-empty list. */
export function median(values: readonly number[]): number {
  if (!values.length) throw new RangeError('median of no values');
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** A stable key for a set of edits: the same edits in any order give the same key. */
export function editsKey(edits: readonly NeuronEdit[] | undefined): string {
  if (!edits?.length) return '';
  return edits
    .map((e) => `${e.layer}:${e.index}:${e.scale}:${e.offset}`)
    .sort()
    .join('|');
}

export interface SessionStep {
  index: number;
  token: string;
  p: number;
  entropy: number;
  topk: { token: string; p: number }[];
  layers?: LayerLens[];
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
  /**
   * The token ids behind the live KV cache: entry i is valid for position i. The
   * next `generate` keeps the longest prefix its prompt shares with these and
   * prefills only the rest — for an agent round, the last tool result and the new
   * turn instead of the whole system prompt and tool schemas again.
   */
  private cached: number[] = [];
  /** `editsKey` of the edits the cached positions were computed under. */
  private editKey = '';

  private constructor(
    readonly runtime: GgufRuntime,
    readonly tokenizer: Tokenizer,
    readonly template: ChatTemplate,
    readonly quant: string,
    /** The template takes `enable_thinking` (Qwen3, SmolLM3): reasoning can be switched. */
    readonly thinking: boolean,
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
    const thinking = typeof t === 'string' && t.includes('enable_thinking');
    return new GgufSession(
      runtime,
      tokenizer,
      template,
      dominantQuant(header),
      thinking,
      gpu.device,
    );
  }

  destroy(): void {
    this.runtime.destroy();
  }

  /** The model's shape, for views that lay out its neurons. */
  get shape(): { layers: number; ffn: number; embd: number; arch: string } {
    const c = this.runtime.config;
    return { layers: c.layers, ffn: c.ffn, embd: c.embd, arch: c.arch };
  }

  /** Put `edits` in place; a different set than the cache was built under drops it. */
  private applyEdits(edits: readonly NeuronEdit[] | undefined): void {
    const key = editsKey(edits);
    if (key === this.editKey) return;
    this.runtime.setNeuronEdits(edits ?? []);
    this.editKey = key;
    this.cached = [];
  }

  /**
   * ‖W_down[:, n]‖₂ for each neuron: how far one unit of its activation moves the
   * residual stream. What an additive dose is divided by.
   */
  async downColumnNorms(neurons: readonly { layer: number; index: number }[]): Promise<number[]> {
    const out: number[] = [];
    for (const n of neurons) out.push(await this.runtime.downColumnNorm(n.layer, n.index));
    // The one-hot pass overwrote the step uniform; the cache itself is untouched.
    return out;
  }

  /**
   * Each layer's typical residual-stream size: the median ‖x‖₂ after the layer over
   * the reference passage's positions after the first (the first token's residual
   * is an outlier in most models). Measured on the unedited model, a token at a
   * time, through the logit lens's norm readout.
   */
  async residualMedians(
    passage: string = REFERENCE_PASSAGE,
    interrupted: () => boolean = () => false,
  ): Promise<number[]> {
    this.applyEdits([]);
    const ids = this.tokenizer.encode(passage);
    if (ids.length < 3) throw new Error('the reference passage is too short to measure');
    const n = Math.min(ids.length, this.runtime.context);
    const perLayer: number[][] = Array.from({ length: this.runtime.config.layers }, () => []);
    const greedy = { ...DEFAULT_SAMPLER, temperature: 0 };
    this.cached = [];
    this.runtime.lens = true;
    try {
      for (let i = 0; i < n; i++) {
        if (interrupted()) throw new Error('interrupted');
        this.runtime.queueStep(ids[i], i);
        this.runtime.queueSample(greedy, 0, 1, 0);
        const layers = await this.runtime.readLens(0);
        if (i > 0) layers.forEach((l, j) => perLayer[j].push(l.norm));
        this.cached.push(ids[i]);
      }
    } catch (err) {
      this.cached = [];
      throw err;
    } finally {
      this.runtime.lens = false;
    }
    return perLayer.map(median);
  }

  /**
   * Every layer's feed-forward activation at one position of a conversation's
   * reply: the position that predicted reply token `index` (index 0 is the
   * prompt's last token). The reply is fed as given — a greedy reply replayed
   * under the edits it was generated with reproduces its own activations — in
   * prefill passes, so this costs about one prompt's prefill.
   */
  async captureActivations(req: {
    messages: SessionGenerate['messages'];
    templateKwargs?: Record<string, unknown>;
    tools?: unknown[];
    reply: string;
    index: number;
    neuronEdits?: NeuronEdit[];
  }): Promise<SessionCapture> {
    this.applyEdits(req.neuronEdits);
    const prompt = this.template.encode(req.messages, { tools: req.tools, kwargs: req.templateKwargs });
    const reply = this.tokenizer.encode(req.reply, { parseSpecial: true });
    if (!Number.isInteger(req.index) || req.index < 0 || req.index > reply.length) {
      throw new Error(`reply token ${req.index} is outside the reply's ${reply.length}`);
    }
    const seq = [...prompt, ...reply];
    const position = prompt.length - 1 + req.index;
    const feed = seq.slice(0, position + 1);
    if (feed.length > this.runtime.context) {
      throw new Error(`position ${position} is outside the ${this.runtime.context}-token context`);
    }
    let reused = 0;
    const reusable = Math.min(this.cached.length, feed.length - 1);
    while (reused < reusable && this.cached[reused] === feed[reused]) reused++;
    this.cached.length = reused;
    this.runtime.setCapture(position);
    try {
      await this.runtime.forward(feed.slice(reused), reused);
      this.cached.push(...feed.slice(reused));
      const activations = await this.runtime.readCapture();
      const { layers, ffn } = this.runtime.config;
      return {
        activations,
        layers,
        ffn,
        position,
        input: this.tokenizer.decode([seq[position]], true),
        predicted: position + 1 < seq.length ? this.tokenizer.decode([seq[position + 1]], true) : '',
      };
    } catch (err) {
      this.cached = [];
      throw err;
    } finally {
      this.runtime.setCapture(null);
    }
  }

  async generate(
    req: SessionGenerate,
    handlers: { onDelta?: (text: string) => void; onStep?: (step: SessionStep) => void },
    interrupted: () => boolean,
    random: () => number = Math.random,
  ): Promise<SessionResult> {
    this.applyEdits(req.neuronEdits);
    this.runtime.lens = req.lens === true;
    try {
      return await this.run(req, handlers, interrupted, random);
    } catch (err) {
      // Whatever was queued before the failure cannot be trusted for reuse.
      this.cached = [];
      throw err;
    } finally {
      this.runtime.lens = false;
    }
  }

  private async run(
    req: SessionGenerate,
    handlers: { onDelta?: (text: string) => void; onStep?: (step: SessionStep) => void },
    interrupted: () => boolean,
    random: () => number,
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

    // Reuse the cache up to the first token that differs. The prompt's last token
    // is always fed, even when the cache already holds it: its logits start the reply.
    let reused = 0;
    const reusable = Math.min(this.cached.length, prompt.length - 1);
    while (reused < reusable && this.cached[reused] === prompt[reused]) reused++;
    this.cached.length = reused;
    const usage = (completionTokens: number): Usage => ({
      promptTokens: prompt.length,
      completionTokens,
      cachedTokens: reused,
    });

    // Prefill one pass at a time, so Stop is heard during a long prompt too. Only
    // the last pass runs the LM head; a single token takes the decode path.
    const batch = this.runtime.batch;
    for (let at = reused; at < prompt.length; at += batch) {
      if (interrupted()) {
        return { text: '', stop: 'interrupt', usage: usage(0), ttftMs: 0, tokensPerSecond: 0 };
      }
      const end = Math.min(at + batch, prompt.length);
      const pass = prompt.slice(at, end);
      if (pass.length === 1) this.runtime.queueStep(pass[0], at);
      else this.runtime.queueBatch(pass, at, end === prompt.length);
      this.cached.push(...pass);
      if (end < prompt.length) await this.device.queue.onSubmittedWorkDone();
    }

    if (req.forced !== undefined) {
      return this.force(req, req.forced, prompt, handlers, interrupted, usage, started);
    }

    const sampler = {
      ...DEFAULT_SAMPLER,
      temperature: req.temperature ?? DEFAULT_SAMPLER.temperature,
    };
    const record = Math.max(0, Math.min(20, req.topK ?? 0));
    const lens = req.lens === true;
    const decoder = new TextDecoder('utf-8', { ignoreBOM: true });
    const generated: number[] = [];
    let text = '';
    let stop: StopReason = 'length';
    let firstAt = 0;

    // Decode with the sampler on the GPU, one step ahead of this loop: the sampler
    // writes each token where the next step's embed reads it, so step i + 1 is
    // queued before token i is read back and the GPU never waits for the host.
    // When the reply ends on EOS, that one speculative step is dropped (its cache
    // entry is not kept).
    const slot = (i: number) => (i % 2) as 0 | 1;
    this.runtime.queueSample(sampler, random(), record, 0);
    for (let i = 0; i < maxNew; i++) {
      const ahead = i + 1 < maxNew && !interrupted();
      if (ahead) {
        this.runtime.queueNext(prompt.length + i);
        this.runtime.queueSample(sampler, random(), record, slot(i + 1));
      }
      const { id, p, entropy, top } = await this.runtime.readSample(slot(i));
      const layers = lens ? await this.runtime.readLens(slot(i)) : undefined;
      if (!firstAt) firstAt = performance.now();
      if (this.tokenizer.eog.has(id)) {
        stop = 'eos';
        break;
      }
      generated.push(id);
      if (ahead) this.cached.push(id);
      const delta = decoder.decode(this.tokenizer.piece(id), { stream: true });
      if (delta) {
        text += delta;
        handlers.onDelta?.(delta);
      }
      if ((record > 0 || lens) && handlers.onStep) {
        const word = (t: { id: number; p: number }) => ({
          token: this.tokenizer.decode([t.id], true),
          p: t.p,
        });
        handlers.onStep({
          index: generated.length - 1,
          token: this.tokenizer.decode([id], true),
          p,
          entropy,
          topk: top.map(word),
          ...(layers && {
            layers: layers.map((l) => ({ norm: l.norm, entropy: l.entropy, top: l.top.map(word) })),
          }),
        });
      }
      if (!ahead) {
        if (i < maxNew - 1) stop = 'interrupt';
        break;
      }
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

  /**
   * Teacher forcing: the reply is `forced`, fed one token at a time after the
   * prompt (already prefilled). At each position the logits come back whole, so
   * the step reports the model's own distribution at temperature 1 — including
   * the probability of the token it is then made to read, however unlikely. With
   * the lens on, each layer's readout comes from the same pass.
   *
   * Not pipelined like sampling: each position's logits are read before the next
   * token is fed. It is an analysis path, run on short texts.
   */
  private async force(
    req: SessionGenerate,
    forced: string,
    prompt: number[],
    handlers: { onDelta?: (text: string) => void; onStep?: (step: SessionStep) => void },
    interrupted: () => boolean,
    usage: (completionTokens: number) => Usage,
    started: number,
  ): Promise<SessionResult> {
    // Special tokens are parsed: the forced text is usually a model's own reply as
    // its steps spelled it (`<s>` and all), and must read back as the same tokens.
    const ids = this.tokenizer.encode(forced, { parseSpecial: true });
    const context = this.runtime.context;
    if (prompt.length + ids.length > context) {
      throw new Error(
        `the prompt and the forced text are ${prompt.length + ids.length} tokens; this model is loaded with a ${context}-token context`,
      );
    }
    const record = Math.max(1, Math.min(20, req.topK ?? 5));
    const lens = req.lens === true;
    const greedy = { ...DEFAULT_SAMPLER, temperature: 0 };
    const word = (t: { id: number; p: number }) => ({
      token: this.tokenizer.decode([t.id], true),
      p: t.p,
    });
    let probs: Float32Array | undefined;
    const decoder = new TextDecoder('utf-8', { ignoreBOM: true });
    let text = '';
    let fed = 0;
    let stop: StopReason = 'length';
    const firstAt = performance.now();
    for (let i = 0; i < ids.length; i++) {
      if (interrupted()) {
        stop = 'interrupt';
        break;
      }
      // The sampler pass is what copies the lens readout out; its pick is unused.
      this.runtime.queueSample(greedy, 0, 1, 0);
      const layers = lens ? await this.runtime.readLens(0) : undefined;
      const dist = distribution(await this.runtime.readLogits(), record, probs);
      probs = dist.probs;
      const id = ids[i];
      const delta = decoder.decode(this.tokenizer.piece(id), { stream: true });
      if (delta) {
        text += delta;
        handlers.onDelta?.(delta);
      }
      handlers.onStep?.({
        index: i,
        token: this.tokenizer.decode([id], true),
        p: dist.probs[id] ?? 0,
        entropy: dist.entropy,
        topk: dist.top.map(word),
        ...(layers && {
          layers: layers.map((l) => ({ norm: l.norm, entropy: l.entropy, top: l.top.map(word) })),
        }),
      });
      this.runtime.queueStep(id, prompt.length + i);
      this.cached.push(id);
      fed++;
    }
    const tail = decoder.decode();
    if (tail) {
      text += tail;
      handlers.onDelta?.(tail);
    }
    const seconds = (performance.now() - firstAt) / 1000;
    return {
      text,
      stop,
      usage: usage(fed),
      ttftMs: Math.round(firstAt - started),
      tokensPerSecond: fed > 1 && seconds > 0 ? fed / seconds : 0,
    };
  }
}
