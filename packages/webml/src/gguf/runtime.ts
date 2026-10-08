/// <reference types="@webgpu/types" />
/**
 * A GGUF model resident on the GPU, and its forward pass.
 *
 * Activations and all accumulation are f32; weights stay in their ggml block
 * layout and are dequantized inside the kernels; the KV cache is f16 (or f32,
 * `kvCache`). A pass's new K and V rows are computed in f32 — normed and rotated
 * there — and then stored into the cache (kv_store.wgsl), as llama.cpp does. Two
 * dispatch lists are built at load, over the same weights, caches and
 * activation buffers:
 *
 * - **decode**: one token. Matrix-vector products (matvec.wgsl) and single-query
 *   attention: the per-token hot path, bound by reading the weights.
 * - **prefill**: up to `batch` prompt tokens in one pass. Tiled matrix products
 *   (matmul.wgsl) that read each weight tile once for the whole batch, and causal
 *   flash attention (attn_prefill.wgsl).
 *
 * Both end in the same **head**: the final norm and the LM head, on the last
 * position only, so a prompt's earlier positions never produce logits. A batch
 * that is not the last of its prompt skips the head altogether.
 *
 * The logits can be read back (`readLogits`) or sampled on the GPU
 * (`queueSample`, sample.wgsl): the sampler writes the chosen id into the token
 * buffer the next decode step's embed reads (`queueNext`), so a step can be queued
 * before the host has read the token before it, and the host reads back only a
 * small record per token (`readSample`).
 *
 * Everything — buffers, uniforms, bind groups — is built once at load. A pass
 * writes its token ids into a GPU buffer and (position, row count) into one small
 * Step uniform, then submits its list again.
 */
import { checkSupport, tensor, type ModelConfig } from './arch';
import { tensorBytes } from './ggml';
import type { ByteSource, GgufHeader, GgufTensor } from './parse';
import { dequantize } from './quant';
import { ropeTable } from './rope';
import type { SamplerOptions } from './sample';
import { paddedSize, tensorChunks, type RowChunk } from '../gpu/chunks';
import { maxBindingBytes, type WebmlDevice } from '../gpu/device';
import { dispatch1d } from '../gpu/dispatch';
import {
  ATTN_CHUNK,
  attnPrefillTile,
  bindGroup,
  blockValues,
  Kernels,
  KERNEL_BLOCK,
  type KvType,
  MATMUL_TILE,
  MAX_GQA_GROUP,
  MAX_HEAD_DIM,
  uniformBuffer,
  WG,
} from '../gpu/kernels';

/** Prompt tokens per prefill pass when the caller does not say. */
export const DEFAULT_BATCH = 256;

/** The most candidates the GPU sampler's top-k, or a step's alternatives, may ask for. */
export const SAMPLE_MAX_K = 64;

/** sample.wgsl's record: id, p, entropy, count, then (id, p) per alternative. */
const RECORD_BYTES = (4 + 2 * SAMPLE_MAX_K) * 4;

/** GPU time per kind of dispatch in one pass, from `profile`. */
export interface PassProfile {
  /** Sum of every dispatch, in ms. Each ran in its own compute pass, so this is
   * somewhat more than the pass takes as one. */
  totalMs: number;
  /** Per kind (`attn_q`, `attention`, `ffn_down`, `embed`, …), slowest first. */
  kinds: { kind: string; ms: number; dispatches: number }[];
}

/** A token the GPU sampler chose, with the distribution it was drawn from. */
export interface SampledToken {
  id: number;
  /** Its probability under that distribution. */
  p: number;
  /** The distribution's entropy, in bits. */
  entropy: number;
  /** The most likely tokens, best first: as many as asked for, never one at p = 0. */
  top: { id: number; p: number }[];
}

export interface LoadOptions {
  /** Positions to allocate the KV cache for; capped at the model's context length. */
  contextLength?: number;
  /**
   * The KV cache's type: f16 (the default, as llama.cpp's) halves its memory and
   * what attention reads per step; f32 is exact, for comparing with llama.cpp run
   * with an f32 cache.
   */
  kvCache?: KvType;
  /** Prompt tokens one prefill pass takes: the row count of every activation buffer. */
  batch?: number;
  /** Bytes uploaded so far, of the total. */
  onProgress?: (loaded: number, total: number) => void;
  /** Override the binding limit tensors are chunked to (tests use a tiny one). */
  maxBindingBytes?: number;
}

/** A weight matrix on the GPU: one buffer per row chunk. */
interface Matrix {
  tensor: GgufTensor;
  /** Values per row (the input width). */
  cols: number;
  rowBytes: number;
  chunks: { chunk: RowChunk; buffer: GPUBuffer }[];
}

interface Dispatch {
  label: string;
  pipeline: GPUComputePipeline;
  group: GPUBindGroup;
  workgroups: [number, number];
}

type Mode = 'decode' | 'prefill';

/** Largest single read from the source while uploading. */
const UPLOAD_PIECE = 16 * 1024 * 1024;

export class GgufRuntime {
  private readonly owned: GPUBuffer[] = [];
  private readonly lists: Record<Mode | 'head', Dispatch[]> = {
    decode: [],
    prefill: [],
    head: [],
  };
  private step!: GPUBuffer;
  private tokens!: GPUBuffer;
  private logits!: GPUBuffer;
  private staging!: GPUBuffer;
  private readonly stepData = new Uint32Array(4);
  private sampler!: Dispatch;
  private samplerParams!: GPUBuffer;
  private sampled!: GPUBuffer;
  /** Two, so the record of step N+1 can be copied while step N's is being read. */
  private readonly sampleStaging: GPUBuffer[] = [];

  private constructor(
    private readonly gpu: WebmlDevice,
    readonly config: ModelConfig,
    /** KV cache positions. */
    readonly context: number,
    /** Prompt tokens per prefill pass. */
    readonly batch: number,
    readonly kvCache: KvType,
  ) {}

  static async load(
    gpu: WebmlDevice,
    header: GgufHeader,
    source: ByteSource,
    options: LoadOptions = {},
  ): Promise<GgufRuntime> {
    const support = checkSupport(header);
    if (!support.ok) throw new Error(`cannot run this model: ${support.reasons.join('; ')}`);
    const config = support.config;
    const context = Math.min(options.contextLength ?? config.contextLength, config.contextLength);
    const batch = Math.max(1, Math.min(options.batch ?? DEFAULT_BATCH, context));
    const rt = new GgufRuntime(gpu, config, context, batch, options.kvCache ?? 'f16');
    // Every pipeline, bind group and the trial passes below are checked together,
    // so a shader or binding mistake fails the load rather than producing garbage.
    gpu.device.pushErrorScope('validation');
    let built: unknown = null;
    try {
      await rt.build(header, source, options);
    } catch (err) {
      built = err;
    }
    const invalid = await gpu.device.popErrorScope();
    if (built || invalid) {
      rt.destroy();
      throw built ?? new Error(`model build failed validation: ${invalid!.message}`);
    }
    return rt;
  }

  /** Free every buffer. The runtime is unusable afterwards. */
  destroy(): void {
    for (const b of this.owned) b.destroy();
    this.owned.length = 0;
  }

  /**
   * Queue `token` at position `pos` through the model, logits included. Passes run
   * in submission order; positions must be fed in order from 0 (or from a position
   * whose earlier cache entries are still valid).
   */
  queueStep(token: number, pos: number): void {
    this.check([token], pos);
    this.submit('decode', [token], pos, true);
  }

  /**
   * Queue a decode step at `pos` on the token the last `queueSample` chose, which
   * is already on the GPU: nothing waits for the host to read it first.
   */
  queueNext(pos: number): void {
    if (!Number.isInteger(pos) || pos < 0 || pos >= this.context) {
      throw new Error(`position ${pos} is outside the ${this.context}-token context`);
    }
    this.submit('decode', null, pos, true);
  }

  /**
   * Queue sampling from the logits of the last pass that computed them, with
   * `random` in [0, 1) for the draw (unused when greedy). The chosen id feeds the
   * next `queueNext`; the record, with `record` alternatives, lands in staging
   * slot `slot` for `readSample`.
   */
  queueSample(options: SamplerOptions, random: number, record: number, slot: 0 | 1): void {
    const greedy = options.temperature <= 0;
    if (!greedy && !(options.topK >= 1 && options.topK <= SAMPLE_MAX_K)) {
      throw new Error(`the GPU sampler takes a top-k of 1 to ${SAMPLE_MAX_K}, not ${options.topK}`);
    }
    if (!(record >= 0 && record <= SAMPLE_MAX_K)) {
      throw new Error(`at most ${SAMPLE_MAX_K} alternatives per token, not ${record}`);
    }
    const { device } = this.gpu;
    const params = new ArrayBuffer(32);
    const u = new Uint32Array(params);
    const f = new Float32Array(params);
    u[0] = this.config.vocab;
    u[1] = greedy ? 0 : options.topK;
    u[2] = record;
    u[3] = greedy ? 1 : 0;
    f[4] = options.temperature;
    f[5] = options.topP;
    f[6] = options.minP;
    f[7] = random;
    device.queue.writeBuffer(this.samplerParams, 0, params);

    const encoder = device.createCommandEncoder({ label: 'sample' });
    const pass = encoder.beginComputePass({ label: 'sample' });
    pass.setPipeline(this.sampler.pipeline);
    pass.setBindGroup(0, this.sampler.group);
    pass.dispatchWorkgroups(1);
    pass.end();
    encoder.copyBufferToBuffer(this.sampled, 0, this.sampleStaging[slot], 0, RECORD_BYTES);
    device.queue.submit([encoder.finish()]);
  }

  /** The record `queueSample` put in `slot` (waits for it). */
  async readSample(slot: 0 | 1): Promise<SampledToken> {
    const staging = this.sampleStaging[slot];
    await staging.mapAsync(GPUMapMode.READ);
    const words = new Uint32Array(staging.getMappedRange().slice(0));
    staging.unmap();
    const floats = new Float32Array(words.buffer);
    const top = [];
    for (let j = 0; j < words[3]; j++) top.push({ id: words[4 + 2 * j], p: floats[5 + 2 * j] });
    return { id: words[0], p: floats[1], entropy: floats[2], top };
  }

  /**
   * Queue up to `batch` tokens at positions pos … pos + n − 1 in one prefill pass.
   * With `logits`, the pass ends with the LM head on its last token; without, it
   * only fills the KV cache (a prompt's earlier batches).
   */
  queueBatch(tokens: ArrayLike<number>, pos: number, logits: boolean): void {
    if (tokens.length > this.batch) {
      throw new Error(`${tokens.length} tokens is more than one ${this.batch}-token pass`);
    }
    this.check(tokens, pos);
    this.submit('prefill', tokens, pos, logits);
  }

  /**
   * The logits after the last queued pass that computed them (waits for it). One
   * read at a time: the staging buffer cannot be mapped twice, and the engine runs
   * one generation at a time anyway.
   */
  async readLogits(): Promise<Float32Array> {
    const { device } = this.gpu;
    const bytes = this.config.vocab * 4;
    const encoder = device.createCommandEncoder({ label: 'logits readback' });
    encoder.copyBufferToBuffer(this.logits, 0, this.staging, 0, bytes);
    device.queue.submit([encoder.finish()]);
    await this.staging.mapAsync(GPUMapMode.READ, 0, bytes);
    const out = new Float32Array(this.staging.getMappedRange(0, bytes).slice(0));
    this.staging.unmap();
    return out;
  }

  /**
   * Feed `tokens` from position `start`; resolves with the logits after the last.
   * One token is a decode step; more are prefill passes of up to `batch` each.
   */
  async forward(tokens: number[], start = 0): Promise<Float32Array> {
    if (!tokens.length) throw new Error('nothing to forward');
    if (tokens.length === 1) {
      this.queueStep(tokens[0], start);
    } else {
      for (let at = 0; at < tokens.length; at += this.batch) {
        const end = Math.min(at + this.batch, tokens.length);
        this.queueBatch(tokens.slice(at, end), start + at, end === tokens.length);
      }
    }
    return this.readLogits();
  }

  /**
   * Time one pass, dispatch by dispatch, with timestamp queries: a decode step at
   * `pos` (n = 1, with the head) or a prefill pass of `n` tokens from `pos`. Token
   * 0 is fed, so the cache entries at those positions are overwritten; a session
   * using this runtime must not reuse them afterwards. Needs `timestamp-query`.
   */
  async profile(mode: Mode, pos: number, n = 1): Promise<PassProfile> {
    const { device } = this.gpu;
    if (!this.gpu.timestamps) throw new Error('this device has no timestamp-query');
    const tokens = new Array<number>(mode === 'decode' ? 1 : n).fill(0);
    this.check(tokens, pos);
    if (tokens.length > this.batch) throw new Error(`${n} tokens is more than one pass`);
    device.queue.writeBuffer(this.tokens, 0, Uint32Array.from(tokens));
    this.stepData[0] = pos;
    this.stepData[1] = tokens.length;
    device.queue.writeBuffer(this.step, 0, this.stepData);

    const list = [...this.lists[mode], ...this.lists.head];
    const querySet = device.createQuerySet({ type: 'timestamp', count: list.length * 2 });
    const bytes = list.length * 2 * 8;
    const resolved = device.createBuffer({
      size: bytes,
      usage: GPUBufferUsage.QUERY_RESOLVE | GPUBufferUsage.COPY_SRC,
    });
    const staging = device.createBuffer({
      size: bytes,
      usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
    });
    try {
      const encoder = device.createCommandEncoder({ label: `profile ${mode}` });
      list.forEach((d, i) => {
        const pass = encoder.beginComputePass({
          label: d.label,
          timestampWrites: {
            querySet,
            beginningOfPassWriteIndex: 2 * i,
            endOfPassWriteIndex: 2 * i + 1,
          },
        });
        pass.setPipeline(d.pipeline);
        pass.setBindGroup(0, d.group);
        pass.dispatchWorkgroups(d.workgroups[0], d.workgroups[1]);
        pass.end();
      });
      encoder.resolveQuerySet(querySet, 0, list.length * 2, resolved, 0);
      encoder.copyBufferToBuffer(resolved, 0, staging, 0, bytes);
      device.queue.submit([encoder.finish()]);
      await staging.mapAsync(GPUMapMode.READ);
      const times = new BigUint64Array(staging.getMappedRange().slice(0));
      staging.unmap();

      const kinds = new Map<string, { ms: number; dispatches: number }>();
      let totalMs = 0;
      list.forEach((d, i) => {
        const ms = Number(times[2 * i + 1] - times[2 * i]) / 1e6;
        const kind = d.label.replace(/^blk\.\d+\./, '').replace(/\.weight$/, '');
        const entry = kinds.get(kind) ?? { ms: 0, dispatches: 0 };
        entry.ms += ms;
        entry.dispatches++;
        kinds.set(kind, entry);
        totalMs += ms;
      });
      return {
        totalMs,
        kinds: [...kinds].map(([kind, v]) => ({ kind, ...v })).sort((a, b) => b.ms - a.ms),
      };
    } finally {
      querySet.destroy();
      resolved.destroy();
      staging.destroy();
    }
  }

  private check(tokens: ArrayLike<number>, pos: number): void {
    if (!tokens.length) throw new Error('nothing to forward');
    const last = pos + tokens.length - 1;
    if (!Number.isInteger(pos) || pos < 0 || last >= this.context) {
      throw new Error(`position ${last} is outside the ${this.context}-token context`);
    }
    for (let i = 0; i < tokens.length; i++) {
      const t = tokens[i];
      if (!Number.isInteger(t) || t < 0 || t >= this.config.vocab) {
        throw new Error(`token ${t} is outside the ${this.config.vocab}-token vocabulary`);
      }
    }
  }

  /** Run `mode`'s list at `pos`: on `tokens`, or on the sampled token (null). */
  private submit(mode: Mode, tokens: ArrayLike<number> | null, pos: number, logits: boolean): void {
    const { device } = this.gpu;
    if (tokens) device.queue.writeBuffer(this.tokens, 0, Uint32Array.from(tokens));
    const n = tokens?.length ?? 1;
    this.stepData[0] = pos;
    this.stepData[1] = n;
    device.queue.writeBuffer(this.step, 0, this.stepData);

    const label = `${mode}@${pos}+${n}`;
    const encoder = device.createCommandEncoder({ label });
    const pass = encoder.beginComputePass({ label });
    for (const d of logits ? [...this.lists[mode], ...this.lists.head] : this.lists[mode]) {
      pass.setPipeline(d.pipeline);
      pass.setBindGroup(0, d.group);
      pass.dispatchWorkgroups(d.workgroups[0], d.workgroups[1]);
    }
    pass.end();
    device.queue.submit([encoder.finish()]);
  }

  // ── building ──────────────────────────────────────────────────────────────

  private buffer(label: string, size: number, usage: number): GPUBuffer {
    const b = this.gpu.device.createBuffer({ label, size: paddedSize(Math.max(size, 4)), usage });
    this.owned.push(b);
    return b;
  }

  private f32Buffer(label: string, floats: number): GPUBuffer {
    return this.buffer(
      label,
      floats * 4,
      GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST,
    );
  }

  private uniform(label: string, fields: Parameters<typeof uniformBuffer>[2]): GPUBuffer {
    const b = uniformBuffer(this.gpu.device, label, fields);
    this.owned.push(b);
    return b;
  }

  private async build(header: GgufHeader, source: ByteSource, options: LoadOptions): Promise<void> {
    const { device } = this.gpu;
    const k = new Kernels(device);
    const c = this.config;
    const B = this.batch;
    const limit = options.maxBindingBytes ?? maxBindingBytes(this.gpu.limits);
    const total = header.tensors.reduce((n, t) => n + (t.bytes ?? 0), 0);
    let loaded = 0;
    const progress = (n: number) => {
      loaded += n;
      options.onProgress?.(loaded, total);
    };

    const read = async (
      t: GgufTensor,
      offset: number,
      length: number,
    ): Promise<Uint8Array<ArrayBuffer>> => {
      const at = header.dataOffset + t.offset + offset;
      const bytes = await source.read(at, length);
      if (bytes.length !== length) {
        throw new Error(`${t.name}: wanted ${length} bytes at ${at}, got ${bytes.length}`);
      }
      return bytes;
    };

    const matrix = async (name: string): Promise<Matrix> => {
      const t = tensor(header, name);
      const cols = t.shape[0];
      if (cols % blockValues(t.type)) {
        throw new Error(`${name}: rows of ${cols} are not a multiple of ${blockValues(t.type)}`);
      }
      const rowBytes = tensorBytes(t.type, cols)!;
      const chunks = [];
      for (const chunk of tensorChunks(t, limit)) {
        const buffer = this.buffer(
          `${name}[${chunk.firstRow}+${chunk.rows}]`,
          chunk.byteLength,
          GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
        );
        for (let off = 0; off < chunk.byteLength; off += UPLOAD_PIECE) {
          const n = Math.min(UPLOAD_PIECE, chunk.byteLength - off);
          const bytes = await read(t, chunk.byteOffset + off, n);
          // writeBuffer wants a multiple of 4; only the chunk's last piece can be short.
          const padded = n % 4 ? new Uint8Array(paddedSize(n)) : bytes;
          if (padded !== bytes) padded.set(bytes);
          device.queue.writeBuffer(buffer, off, padded);
          progress(n);
        }
        chunks.push({ chunk, buffer });
      }
      return { tensor: t, cols, rowBytes, chunks };
    };

    /** A 1-D tensor (norm weights), dequantized here and uploaded as f32. */
    const vector = async (name: string): Promise<GPUBuffer> => {
      const t = tensor(header, name);
      const values = dequantize(t.type, await read(t, 0, t.bytes!), 0, t.elements);
      const buffer = this.f32Buffer(name, t.elements);
      device.queue.writeBuffer(buffer, 0, values);
      progress(t.bytes!);
      return buffer;
    };

    // Activations: `B` rows each; a decode step uses row 0. Then the caches.
    const qDim = c.heads * c.headDim;
    const kvDim = c.kvHeads * c.headDim;
    const x = this.f32Buffer('x', B * c.embd);
    const h = this.f32Buffer('h', B * c.embd);
    const q = this.f32Buffer('q', B * qDim);
    const att = this.f32Buffer('att', B * qDim);
    // The pass's new K and V rows, before they go into the cache.
    const kNew = this.f32Buffer('k_new', B * kvDim);
    const vNew = this.f32Buffer('v_new', B * kvDim);
    const gate = this.f32Buffer('gate', B * c.ffn);
    const up = this.f32Buffer('up', B * c.ffn);
    const act = this.f32Buffer('act', B * c.ffn);
    if (c.headDim > MAX_HEAD_DIM) {
      throw new Error(`heads of ${c.headDim} are larger than the engine's ${MAX_HEAD_DIM}`);
    }
    if (c.heads / c.kvHeads > MAX_GQA_GROUP) {
      throw new Error(
        `${c.heads / c.kvHeads} query heads per KV head is more than the engine's ${MAX_GQA_GROUP}`,
      );
    }
    // Decode attention's per-chunk partials: weighted sum, max and sum per head per chunk.
    const splits = Math.ceil(this.context / ATTN_CHUNK);
    const partials = this.f32Buffer('attention partials', c.heads * splits * (c.headDim + 2));
    this.logits = this.f32Buffer('logits', c.vocab);
    this.staging = this.buffer(
      'logits staging',
      c.vocab * 4,
      GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
    );
    this.step = this.buffer('step', 16, GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST);
    this.tokens = this.buffer('tokens', B * 4, GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST);
    this.samplerParams = this.buffer(
      'sampler params',
      32,
      GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
    );
    this.sampled = this.buffer(
      'sampled',
      RECORD_BYTES,
      GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
    );
    for (const slot of [0, 1]) {
      this.sampleStaging.push(
        this.buffer(
          `sampled staging ${slot}`,
          RECORD_BYTES,
          GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
        ),
      );
    }
    {
      const pipeline = k.sample();
      this.sampler = {
        label: 'sample',
        pipeline,
        group: bindGroup(device, pipeline, {
          0: this.logits,
          1: this.tokens,
          2: this.sampled,
          3: this.samplerParams,
        }),
        workgroups: [1, 1],
      };
    }
    const rope = this.f32Buffer('rope', this.context * c.ropeDims);
    let factors: Float32Array | undefined;
    if (c.ropeFreqs) {
      const t = tensor(header, 'rope_freqs.weight');
      factors = dequantize(t.type, await read(t, 0, t.bytes!), 0, t.elements);
      progress(t.bytes!);
    }
    device.queue.writeBuffer(rope, 0, ropeTable(this.context, c.ropeDims, c.ropeBase, factors));

    const maxGroups = this.gpu.limits.maxComputeWorkgroupsPerDimension;
    const rowsOf = (mode: Mode) => (mode === 'decode' ? 1 : B);

    /** output = input · Wᵀ, row by row; with `accumulate`, output += input · Wᵀ. */
    const project = (
      mode: Mode,
      m: Matrix,
      input: GPUBuffer,
      output: GPUBuffer,
      accumulate = false,
    ): void => {
      const width = m.tensor.shape[1];
      for (const { chunk, buffer } of m.chunks) {
        const bindings = (pipeline: GPUComputePipeline, params: GPUBuffer) =>
          bindGroup(device, pipeline, { 0: buffer, 1: input, 2: output, 3: params, 4: this.step });
        if (mode === 'decode') {
          const pipeline = k.matvec(m.tensor.type);
          const params = this.uniform(`${m.tensor.name}.matvec`, [
            chunk.rows,
            m.cols / KERNEL_BLOCK[m.tensor.type].unit,
            m.rowBytes,
            chunk.firstRow,
            0,
            accumulate ? 1 : 0,
          ]);
          this.lists.decode.push({
            label: m.tensor.name,
            pipeline,
            group: bindings(pipeline, params),
            workgroups: dispatch1d(chunk.rows, WG.matvecRows, maxGroups),
          });
        } else {
          const pipeline = k.matmul(m.tensor.type);
          const params = this.uniform(`${m.tensor.name}.matmul`, [
            chunk.rows,
            m.cols,
            m.rowBytes,
            chunk.firstRow,
            0,
            width,
            accumulate ? 1 : 0,
          ]);
          this.lists.prefill.push({
            label: m.tensor.name,
            pipeline,
            group: bindings(pipeline, params),
            workgroups: [
              Math.ceil(chunk.rows / MATMUL_TILE.rows),
              Math.ceil(B / MATMUL_TILE.batch),
            ],
          });
        }
      }
    };

    const rmsnorm = (
      list: Dispatch[],
      rows: number,
      input: GPUBuffer,
      weight: GPUBuffer,
      output: GPUBuffer,
      label: string,
      last = false,
    ) => {
      const pipeline = k.rmsnorm();
      const params = this.uniform(`${label}.params`, [c.embd, { f32: c.rmsEps }, last ? 1 : 0]);
      list.push({
        label,
        pipeline,
        group: bindGroup(device, pipeline, {
          0: input,
          1: weight,
          2: output,
          3: params,
          4: this.step,
        }),
        workgroups: [last ? 1 : rows, 1],
      });
    };

    /** Qwen3's per-head RMS norm on Q or K, in place, before rope. */
    const headNorm = (
      mode: Mode,
      v: GPUBuffer,
      heads: number,
      weight: GPUBuffer,
      label: string,
    ) => {
      const pipeline = k.headnorm();
      const width = heads * c.headDim;
      const params = this.uniform(`${label}.params`, [c.headDim, { f32: c.rmsEps }, 0, 0, width]);
      this.lists[mode].push({
        label,
        pipeline,
        group: bindGroup(device, pipeline, { 0: v, 1: weight, 2: params, 3: this.step }),
        workgroups: [heads, rowsOf(mode)],
      });
    };

    const ropeOn = (mode: Mode, v: GPUBuffer, heads: number, label: string) => {
      const pipeline = k.rope();
      const width = heads * c.headDim;
      const params = this.uniform(`${label}.params`, [
        heads,
        c.headDim,
        c.ropeDims,
        0,
        0,
        c.rope === 'neox' ? 1 : 0,
        width,
      ]);
      this.lists[mode].push({
        label,
        pipeline,
        group: bindGroup(device, pipeline, { 0: v, 1: rope, 2: params, 3: this.step }),
        workgroups: dispatch1d((rowsOf(mode) * heads * c.ropeDims) / 2, WG.rope, maxGroups),
      });
    };

    const elementwise = (
      mode: Mode,
      pipeline: GPUComputePipeline,
      buffers: GPUBuffer[],
      n: number,
      label: string,
    ) => {
      const params = this.uniform(`${label}.params`, [n]);
      const bindings: Record<number, GPUBuffer> = {};
      [...buffers, params, this.step].forEach((b, i) => (bindings[i] = b));
      this.lists[mode].push({
        label,
        pipeline,
        group: bindGroup(device, pipeline, bindings),
        workgroups: dispatch1d(rowsOf(mode) * n, WG.swiglu, maxGroups),
      });
    };

    /** The pass's new K and V rows into the cache, at its positions. */
    const store = (mode: Mode, kCache: GPUBuffer, vCache: GPUBuffer, label: string) => {
      const pipeline = k.kvStore(this.kvCache);
      const params = this.uniform(`${label}.params`, [kvDim]);
      this.lists[mode].push({
        label,
        pipeline,
        group: bindGroup(device, pipeline, {
          0: kNew,
          1: vNew,
          2: kCache,
          3: vCache,
          4: params,
          5: this.step,
        }),
        workgroups: dispatch1d((rowsOf(mode) * kvDim) / 2, WG.kvStore, maxGroups),
      });
    };

    const attention = (mode: Mode, kCache: GPUBuffer, vCache: GPUBuffer, label: string) => {
      const group = c.heads / c.kvHeads;
      if (mode === 'decode') {
        // Chunks of ATTN_CHUNK positions in parallel, one workgroup per KV head
        // serving its query heads; then one merge per query head.
        const pipeline = k.attention(this.kvCache, c.headDim, group);
        const params = this.uniform(`${label}.params`, [kvDim, splits, { f32: c.attentionScale }]);
        this.lists.decode.push({
          label,
          pipeline,
          group: bindGroup(device, pipeline, {
            0: q,
            1: kCache,
            2: vCache,
            3: partials,
            4: params,
            5: this.step,
          }),
          workgroups: [c.kvHeads, splits],
        });
        const combine = k.attnCombine();
        const combineParams = this.uniform(`${label}.combine.params`, [c.headDim, splits]);
        this.lists.decode.push({
          label: `${label}_combine`,
          pipeline: combine,
          group: bindGroup(device, combine, {
            0: partials,
            1: att,
            2: combineParams,
            3: this.step,
          }),
          workgroups: [c.heads, 1],
        });
      } else {
        const pipeline = k.attnPrefill(c.headDim, this.kvCache);
        const tile = attnPrefillTile(c.headDim);
        const params = this.uniform(`${label}.params`, [
          qDim,
          kvDim,
          group,
          { f32: c.attentionScale },
        ]);
        this.lists.prefill.push({
          label,
          pipeline,
          group: bindGroup(device, pipeline, {
            0: q,
            1: kCache,
            2: vCache,
            3: att,
            4: params,
            5: this.step,
          }),
          workgroups: [c.heads, Math.ceil(B / tile)],
        });
      }
    };

    // The embedding: one dispatch per chunk, each writing the tokens it holds.
    const embedding = await matrix('token_embd.weight');
    for (const mode of ['decode', 'prefill'] as const) {
      const pipeline = k.embed(embedding.tensor.type);
      for (const { chunk, buffer } of embedding.chunks) {
        const params = this.uniform('embed.params', [
          c.embd,
          embedding.rowBytes,
          chunk.firstRow,
          chunk.rows,
        ]);
        this.lists[mode].push({
          label: 'embed',
          pipeline,
          group: bindGroup(device, pipeline, {
            0: buffer,
            2: x,
            3: params,
            4: this.step,
            5: this.tokens,
          }),
          workgroups: dispatch1d(rowsOf(mode) * c.embd, WG.embed, maxGroups),
        });
      }
    }

    for (let l = 0; l < c.layers; l++) {
      const p = `blk.${l}.`;
      const cacheBytes = this.context * kvDim * (this.kvCache === 'f16' ? 2 : 4);
      const kCache = this.buffer(`${p}k_cache`, cacheBytes, GPUBufferUsage.STORAGE);
      const vCache = this.buffer(`${p}v_cache`, cacheBytes, GPUBufferUsage.STORAGE);
      const w = {
        attnNorm: await vector(`${p}attn_norm.weight`),
        q: await matrix(`${p}attn_q.weight`),
        k: await matrix(`${p}attn_k.weight`),
        v: await matrix(`${p}attn_v.weight`),
        qNorm: c.qkNorm ? await vector(`${p}attn_q_norm.weight`) : null,
        kNorm: c.qkNorm ? await vector(`${p}attn_k_norm.weight`) : null,
        o: await matrix(`${p}attn_output.weight`),
        ffnNorm: await vector(`${p}ffn_norm.weight`),
        gate: await matrix(`${p}ffn_gate.weight`),
        up: await matrix(`${p}ffn_up.weight`),
        down: await matrix(`${p}ffn_down.weight`),
      };

      for (const mode of ['decode', 'prefill'] as const) {
        const list = this.lists[mode];
        const rows = rowsOf(mode);
        rmsnorm(list, rows, x, w.attnNorm, h, `${p}attn_norm`);
        project(mode, w.q, h, q);
        project(mode, w.k, h, kNew);
        project(mode, w.v, h, vNew);
        if (w.qNorm && w.kNorm) {
          headNorm(mode, q, c.heads, w.qNorm, `${p}q_norm`);
          headNorm(mode, kNew, c.kvHeads, w.kNorm, `${p}k_norm`);
        }
        ropeOn(mode, q, c.heads, `${p}rope_q`);
        ropeOn(mode, kNew, c.kvHeads, `${p}rope_k`);
        store(mode, kCache, vCache, `${p}kv_store`);
        attention(mode, kCache, vCache, `${p}attention`);
        // The residual adds are fused into the projections that feed them.
        project(mode, w.o, att, x, true);

        rmsnorm(list, rows, x, w.ffnNorm, h, `${p}ffn_norm`);
        project(mode, w.gate, h, gate);
        project(mode, w.up, h, up);
        elementwise(mode, k.swiglu(), [gate, up, act], c.ffn, `${p}swiglu`);
        project(mode, w.down, act, x, true);
      }
    }

    // The head: the last row's final norm into row 0 of h, then the LM head as a
    // matvec — the decode list's projection, which reads row 0.
    rmsnorm(this.lists.head, 1, x, await vector('output_norm.weight'), h, 'output_norm', true);
    const lmHead = c.tiedEmbeddings ? embedding : await matrix('output.weight');
    const before = this.lists.decode.length;
    project('decode', lmHead, h, this.logits);
    this.lists.head.push(...this.lists.decode.splice(before));

    // One trial pass of each kind, inside the caller's error scope. They write
    // cache position 0, which the first real pass overwrites.
    this.queueStep(0, 0);
    this.queueBatch([0], 0, true);
    this.queueSample({ temperature: 0.8, topK: 40, topP: 0.95, minP: 0.05 }, 0.5, 4, 0);
    this.queueNext(0);
    await device.queue.onSubmittedWorkDone();
  }
}
