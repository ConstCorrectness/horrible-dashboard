/**
 * The model worker: transformers.js on WebGPU, off the UI thread. Protocol in
 * ../protocol.ts. One model at a time; one generation at a time (a second
 * `generate` while one runs is refused, not queued — the caller owns ordering).
 */
import {
  AutoModelForCausalLM,
  AutoTokenizer,
  InterruptableStoppingCriteria,
  LogitsProcessor,
  LogitsProcessorList,
  TextStreamer,
  env,
  type PreTrainedModel,
  type PreTrainedTokenizer,
  type Tensor,
} from '@huggingface/transformers';

import { distribution, type Distribution } from '../distribution';
import type {
  Device,
  Dtype,
  GenerateRequest,
  LoadRequest,
  StopReason,
  WorkerEvent,
  WorkerRequest,
} from '../protocol';

// Hub only: there is no local model directory to probe in a browser.
env.allowLocalModels = false;

const post = (event: WorkerEvent) => (self as unknown as Worker).postMessage(event);

interface Loaded {
  model: string;
  dtype: Dtype;
  device: Device;
  tokenizer: PreTrainedTokenizer;
  lm: PreTrainedModel;
}

let loaded: Loaded | null = null;
let loading: Promise<void> | null = null;
let generating: string | null = null;
const stopping = new InterruptableStoppingCriteria();

/** Records the distribution each token is sampled from; runs after the model's own processors. */
class Recorder extends LogitsProcessor {
  last: Distribution | null = null;
  private buf: Float32Array | undefined;
  constructor(private readonly k: number) {
    super();
  }
  _call(_inputIds: bigint[][], logits: Tensor): Tensor {
    const vocab = logits.dims.at(-1) ?? 0;
    const row = (logits.data as Float32Array).subarray(0, vocab);
    this.last = distribution(row, this.k, this.buf);
    this.buf = this.last.probs;
    return logits;
  }
}

function processorList(recorder: Recorder): LogitsProcessorList {
  const list = new LogitsProcessorList();
  list.push(recorder);
  return list;
}

async function load(req: LoadRequest): Promise<void> {
  if (
    loaded &&
    loaded.model === req.model &&
    loaded.dtype === req.dtype &&
    loaded.device === req.device
  ) {
    post({ type: 'ready', model: req.model, dtype: req.dtype, device: req.device, loadMs: 0 });
    return;
  }
  await unload(false);
  const started = performance.now();
  const lastSent = new Map<string, number>();
  const progress_callback = (info: {
    status: string;
    file?: string;
    loaded?: number;
    total?: number;
  }) => {
    if (!info.file) return;
    // Byte progress arrives per chunk; ten updates a second per file is plenty.
    if (info.status === 'progress') {
      const now = performance.now();
      if (now - (lastSent.get(info.file) ?? 0) < 100) return;
      lastSent.set(info.file, now);
    }
    post({
      type: 'progress',
      status: info.status,
      file: info.file,
      loaded: info.loaded ?? 0,
      total: info.total ?? 0,
    });
  };
  const tokenizer = await AutoTokenizer.from_pretrained(req.model, { progress_callback });
  const lm = await AutoModelForCausalLM.from_pretrained(req.model, {
    dtype: req.dtype,
    device: req.device,
    progress_callback,
  });
  // One token through the graph compiles the WebGPU pipelines now, so the first real
  // reply's TTFT measures the model rather than shader compilation.
  post({ type: 'progress', status: 'warmup', file: 'shaders', loaded: 0, total: 0 });
  const warm = tokenizer('a') as unknown as Record<string, unknown>;
  await lm.generate({ ...warm, max_new_tokens: 1 });
  loaded = { model: req.model, dtype: req.dtype, device: req.device, tokenizer, lm };
  post({
    type: 'ready',
    model: req.model,
    dtype: req.dtype,
    device: req.device,
    loadMs: Math.round(performance.now() - started),
  });
}

async function unload(announce = true): Promise<void> {
  const previous = loaded;
  loaded = null;
  if (previous) await previous.lm.dispose();
  if (announce) post({ type: 'unloaded' });
}

/** Prompt positions forwarded per prefill step. See `prefill`. */
const PREFILL_CHUNK = 256;

interface KvCache {
  dispose?: () => Promise<void> | void;
}

/**
 * Run a long prompt into the KV cache a chunk at a time.
 *
 * These ONNX exports have no `num_logits_to_keep` input, so one forward over a whole
 * prompt returns logits for *every* position: an agent turn's 4k-token prompt times
 * a 152k vocabulary is 2.4 GB of float32, past ONNX Runtime Web's 32-bit heap, and the
 * run dies with `std::bad_alloc`. Chunks bound that at PREFILL_CHUNK × vocab.
 *
 * Each step is a one-token `generate` whose sampled token is thrown away: the cache it
 * returns covers exactly the positions it forwarded, and the next call — handed the
 * longer prefix plus that cache — forwards only the new chunk. Stops one chunk short,
 * so the real generation forwards the remainder and samples from it.
 */
async function prefill(
  lm: PreTrainedModel,
  inputs: { input_ids: Tensor; attention_mask?: Tensor },
): Promise<KvCache | null> {
  const n = inputs.input_ids.dims.at(-1) ?? 0;
  if (n <= PREFILL_CHUNK) return null;
  let past: KvCache | null = null;
  for (let end = PREFILL_CHUNK; end < n; end += PREFILL_CHUNK) {
    if (stopping.interrupted) break;
    const out = (await lm.generate({
      input_ids: inputs.input_ids.slice(null, [0, end]),
      ...(inputs.attention_mask
        ? { attention_mask: inputs.attention_mask.slice(null, [0, end]) }
        : {}),
      past_key_values: past,
      max_new_tokens: 1,
      do_sample: false,
      return_dict_in_generate: true,
    } as Parameters<typeof lm.generate>[0])) as unknown as { past_key_values: KvCache };
    past = out.past_key_values;
  }
  return past;
}

async function generate(req: GenerateRequest): Promise<void> {
  if (!loaded) throw new Error('no model loaded');
  const { tokenizer, lm } = loaded;
  const started = performance.now();

  const inputs = tokenizer.apply_chat_template(req.messages, {
    add_generation_prompt: true,
    return_dict: true,
    tools: req.tools?.length ? req.tools : null,
    // Anything else is handed to the template as a variable (`enable_thinking`, …).
    ...(req.templateKwargs ?? {}),
  } as Parameters<typeof tokenizer.apply_chat_template>[1]) as unknown as {
    input_ids: Tensor;
    attention_mask?: Tensor;
  } & Record<string, unknown>;
  const promptTokens = inputs.input_ids.dims.at(-1) ?? 0;

  const k = Math.max(0, Math.min(20, req.topK ?? 0));
  const recorder = k > 0 ? new Recorder(k) : null;
  const generated: bigint[] = [];
  let firstAt = 0;

  const streamer = new TextStreamer(tokenizer, {
    skip_prompt: true,
    skip_special_tokens: true,
    callback_function: (text: string) => {
      if (text) post({ type: 'delta', id: req.id, text });
    },
    token_callback_function: (tokens: bigint[]) => {
      if (!firstAt) firstAt = performance.now();
      for (const t of tokens) {
        const index = generated.length;
        generated.push(t);
        const dist = recorder?.last;
        if (!dist) continue;
        const id = Number(t);
        post({
          type: 'step',
          id: req.id,
          index,
          token: tokenizer.decode([t], { skip_special_tokens: false }),
          p: dist.probs[id] ?? 0,
          entropy: dist.entropy,
          topk: dist.top.map((alt) => ({
            token: tokenizer.decode([alt.id], { skip_special_tokens: false }),
            p: alt.p,
          })),
        });
      }
    },
  });

  stopping.reset();
  const maxNewTokens = req.maxNewTokens ?? 1024;
  const temperature = req.temperature ?? 0.7;
  const past = await prefill(lm, inputs);
  try {
    // Stopped while the prompt was still going in: the cache covers only part of it,
    // and generating from a truncated prompt would be answering a different question.
    if (!stopping.interrupted) {
      await lm.generate({
        ...inputs,
        // Only when there is one: passing the key at all keeps the cache alive afterwards.
        ...(past ? { past_key_values: past } : {}),
        max_new_tokens: maxNewTokens,
        do_sample: temperature > 0,
        ...(temperature > 0 ? { temperature } : {}),
        streamer,
        stopping_criteria: stopping,
        ...(recorder ? { logits_processor: processorList(recorder) } : {}),
      } as Parameters<typeof lm.generate>[0]);
    }
  } finally {
    await past?.dispose?.();
  }

  const ended = performance.now();
  const stop: StopReason = stopping.interrupted
    ? 'interrupt'
    : generated.length >= maxNewTokens
      ? 'length'
      : 'eos';
  const decodeSeconds = firstAt ? (ended - firstAt) / 1000 : 0;
  post({
    type: 'done',
    id: req.id,
    // `decode([])` throws rather than returning '' — and an interrupt during prefill
    // generates nothing.
    text: generated.length ? tokenizer.decode(generated, { skip_special_tokens: true }) : '',
    stop,
    usage: { promptTokens, completionTokens: generated.length },
    ttftMs: firstAt ? Math.round(firstAt - started) : 0,
    tokensPerSecond:
      generated.length > 1 && decodeSeconds > 0 ? (generated.length - 1) / decodeSeconds : 0,
  });
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message;
  // ORT surfaces some WebGPU failures as bare numbers (a pointer into wasm memory).
  if (typeof err === 'number') return `ONNX Runtime error (code ${err}) — often out of GPU memory`;
  return String(err);
}

self.addEventListener('message', (event: MessageEvent<WorkerRequest>) => {
  const req = event.data;
  switch (req.type) {
    case 'load':
      loading = (loading ?? Promise.resolve())
        .then(() => load(req))
        .catch((err) => post({ type: 'error', message: describe(err) }))
        .finally(() => (loading = null));
      return;
    case 'unload':
      stopping.interrupt();
      void unload().catch((err) => post({ type: 'error', message: describe(err) }));
      return;
    case 'interrupt':
      stopping.interrupt();
      return;
    case 'generate':
      if (generating) {
        post({ type: 'error', id: req.id, message: 'the model is busy with another reply' });
        return;
      }
      generating = req.id;
      void (loading ?? Promise.resolve())
        .then(() => generate(req))
        .catch((err) => post({ type: 'error', id: req.id, message: describe(err) }))
        .finally(() => (generating = null));
      return;
  }
});
