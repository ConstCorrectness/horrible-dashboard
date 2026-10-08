/// <reference types="@webgpu/types" />
/**
 * A GGUF model resident on the GPU, and its forward pass, one token at a time.
 *
 * Correct and slow (6.1–6.3). Activations, the KV cache and all accumulation are f32;
 * weights stay in their ggml block layout and are dequantized inside the kernels.
 * A prompt is fed as a loop of single-token steps (no batched prefill yet), and
 * the logits come back to the CPU for sampling.
 *
 * Everything a token needs — buffers, uniforms, bind groups — is built once at
 * load. A step writes the position and token row into one small Step buffer and
 * submits the same list of dispatches again.
 */
import { checkSupport, tensor, type ModelConfig } from './arch';
import { tensorBytes } from './ggml';
import type { ByteSource, GgufHeader, GgufTensor } from './parse';
import { dequantize } from './quant';
import { ropeTable } from './rope';
import { paddedSize, tensorChunks, type RowChunk } from '../gpu/chunks';
import { maxBindingBytes, type WebmlDevice } from '../gpu/device';
import { dispatch1d } from '../gpu/dispatch';
import { bindGroup, blockValues, Kernels, KERNEL_BLOCK, uniformBuffer, WG } from '../gpu/kernels';

export interface LoadOptions {
  /** Positions to allocate the KV cache for; capped at the model's context length. */
  contextLength?: number;
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

/** Largest single read from the source while uploading. */
const UPLOAD_PIECE = 16 * 1024 * 1024;

export class GgufRuntime {
  private readonly owned: GPUBuffer[] = [];
  private readonly dispatches: Dispatch[] = [];
  /** One embed dispatch per embedding chunk; the token picks which. */
  private readonly embeds: { chunk: RowChunk; dispatch: Dispatch }[] = [];
  private step!: GPUBuffer;
  private logits!: GPUBuffer;
  private staging!: GPUBuffer;
  private readonly stepData = new Uint32Array(4);

  private constructor(
    private readonly gpu: WebmlDevice,
    readonly config: ModelConfig,
    /** KV cache positions. */
    readonly context: number,
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
    const rt = new GgufRuntime(gpu, config, context);
    // Every pipeline, bind group and the trial step below are checked together, so
    // a shader or binding mistake fails the load rather than producing garbage.
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
   * Queue `token` at position `pos` through the model. Steps run in submission
   * order; positions must be fed in order from 0 (or from a position whose
   * earlier cache entries are still valid).
   */
  queueStep(token: number, pos: number): void {
    const { device } = this.gpu;
    if (!Number.isInteger(pos) || pos < 0 || pos >= this.context) {
      throw new Error(`position ${pos} is outside the ${this.context}-token context`);
    }
    if (!Number.isInteger(token) || token < 0 || token >= this.config.vocab) {
      throw new Error(`token ${token} is outside the ${this.config.vocab}-token vocabulary`);
    }
    const embed = this.embeds.find(
      (e) => token >= e.chunk.firstRow && token < e.chunk.firstRow + e.chunk.rows,
    )!;
    this.stepData[0] = pos;
    this.stepData[1] = token - embed.chunk.firstRow;
    device.queue.writeBuffer(this.step, 0, this.stepData);

    const encoder = device.createCommandEncoder({ label: `token@${pos}` });
    const pass = encoder.beginComputePass({ label: `token@${pos}` });
    for (const d of [embed.dispatch, ...this.dispatches]) {
      pass.setPipeline(d.pipeline);
      pass.setBindGroup(0, d.group);
      pass.dispatchWorkgroups(d.workgroups[0], d.workgroups[1]);
    }
    pass.end();
    device.queue.submit([encoder.finish()]);
  }

  /**
   * The logits after the last queued step (waits for it to finish). One read at a
   * time: the staging buffer cannot be mapped twice, and the engine runs one
   * generation at a time anyway.
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

  /** Feed `tokens` from position `start`; resolves with the logits after the last. */
  async forward(tokens: number[], start = 0): Promise<Float32Array> {
    if (!tokens.length) throw new Error('nothing to forward');
    tokens.forEach((t, i) => this.queueStep(t, start + i));
    return this.readLogits();
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

    // Activations and caches.
    const qDim = c.heads * c.headDim;
    const kvDim = c.kvHeads * c.headDim;
    const x = this.f32Buffer('x', c.embd);
    const h = this.f32Buffer('h', c.embd);
    const q = this.f32Buffer('q', qDim);
    const att = this.f32Buffer('att', qDim);
    const o = this.f32Buffer('o', c.embd);
    const gate = this.f32Buffer('gate', c.ffn);
    const up = this.f32Buffer('up', c.ffn);
    const act = this.f32Buffer('act', c.ffn);
    const scores = this.f32Buffer('scores', c.heads * this.context);
    this.logits = this.f32Buffer('logits', c.vocab);
    this.staging = this.buffer(
      'logits staging',
      c.vocab * 4,
      GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
    );
    this.step = this.buffer('step', 16, GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST);
    const rope = this.f32Buffer('rope', this.context * c.ropeDims);
    let factors: Float32Array | undefined;
    if (c.ropeFreqs) {
      const t = tensor(header, 'rope_freqs.weight');
      factors = dequantize(t.type, await read(t, 0, t.bytes!), 0, t.elements);
      progress(t.bytes!);
    }
    device.queue.writeBuffer(rope, 0, ropeTable(this.context, c.ropeDims, c.ropeBase, factors));

    const maxGroups = this.gpu.limits.maxComputeWorkgroupsPerDimension;
    const add = (d: Dispatch) => this.dispatches.push(d);

    const matvec = (
      m: Matrix,
      input: GPUBuffer,
      output: GPUBuffer,
      outBase = 0,
      outPosStride = 0,
    ): void => {
      const pipeline = k.matvec(m.tensor.type);
      const block = KERNEL_BLOCK[m.tensor.type];
      for (const { chunk, buffer } of m.chunks) {
        const params = this.uniform(`${m.tensor.name}.params`, [
          chunk.rows,
          m.cols / block.unit,
          m.rowBytes,
          outBase + chunk.firstRow,
          outPosStride,
        ]);
        add({
          label: m.tensor.name,
          pipeline,
          group: bindGroup(device, pipeline, {
            0: buffer,
            1: input,
            2: output,
            3: params,
            4: this.step,
          }),
          workgroups: dispatch1d(chunk.rows, 1, maxGroups),
        });
      }
    };

    const rmsnorm = (input: GPUBuffer, weight: GPUBuffer, output: GPUBuffer, label: string) => {
      const pipeline = k.rmsnorm();
      const params = this.uniform(`${label}.params`, [c.embd, { f32: c.rmsEps }]);
      add({
        label,
        pipeline,
        group: bindGroup(device, pipeline, { 0: input, 1: weight, 2: output, 3: params }),
        workgroups: [1, 1],
      });
    };

    /** Qwen3's per-head RMS norm on Q or K, in place, before rope. */
    const headNorm = (
      v: GPUBuffer,
      heads: number,
      posStride: number,
      weight: GPUBuffer,
      label: string,
    ) => {
      const pipeline = k.headnorm();
      const params = this.uniform(`${label}.params`, [c.headDim, { f32: c.rmsEps }, 0, posStride]);
      add({
        label,
        pipeline,
        group: bindGroup(device, pipeline, { 0: v, 1: weight, 2: params, 3: this.step }),
        workgroups: [heads, 1],
      });
    };

    const ropeOn = (v: GPUBuffer, heads: number, posStride: number, label: string) => {
      const pipeline = k.rope();
      const params = this.uniform(`${label}.params`, [
        heads,
        c.headDim,
        c.ropeDims,
        0,
        posStride,
        c.rope === 'neox' ? 1 : 0,
      ]);
      add({
        label,
        pipeline,
        group: bindGroup(device, pipeline, { 0: v, 1: rope, 2: params, 3: this.step }),
        workgroups: dispatch1d((heads * c.ropeDims) / 2, WG.rope, maxGroups),
      });
    };

    const elementwise = (
      pipeline: GPUComputePipeline,
      buffers: GPUBuffer[],
      n: number,
      label: string,
    ) => {
      const params = this.uniform(`${label}.params`, [n]);
      const bindings: Record<number, GPUBuffer> = {};
      [...buffers, params].forEach((b, i) => (bindings[i] = b));
      add({
        label,
        pipeline,
        group: bindGroup(device, pipeline, bindings),
        workgroups: dispatch1d(n, WG.swiglu, maxGroups),
      });
    };

    // The embedding: one dispatch per chunk, chosen per token.
    const embedding = await matrix('token_embd.weight');
    {
      const pipeline = k.embed(embedding.tensor.type);
      for (const { chunk, buffer } of embedding.chunks) {
        const params = this.uniform('embed.params', [c.embd, embedding.rowBytes]);
        this.embeds.push({
          chunk,
          dispatch: {
            label: 'embed',
            pipeline,
            group: bindGroup(device, pipeline, { 0: buffer, 2: x, 3: params, 4: this.step }),
            workgroups: dispatch1d(c.embd, WG.embed, maxGroups),
          },
        });
      }
    }

    for (let l = 0; l < c.layers; l++) {
      const p = `blk.${l}.`;
      const kCache = this.f32Buffer(`${p}k_cache`, this.context * kvDim);
      const vCache = this.f32Buffer(`${p}v_cache`, this.context * kvDim);

      rmsnorm(x, await vector(`${p}attn_norm.weight`), h, `${p}attn_norm`);
      matvec(await matrix(`${p}attn_q.weight`), h, q);
      matvec(await matrix(`${p}attn_k.weight`), h, kCache, 0, kvDim);
      matvec(await matrix(`${p}attn_v.weight`), h, vCache, 0, kvDim);
      if (c.qkNorm) {
        headNorm(q, c.heads, 0, await vector(`${p}attn_q_norm.weight`), `${p}q_norm`);
        headNorm(kCache, c.kvHeads, kvDim, await vector(`${p}attn_k_norm.weight`), `${p}k_norm`);
      }
      ropeOn(q, c.heads, 0, `${p}rope_q`);
      ropeOn(kCache, c.kvHeads, kvDim, `${p}rope_k`);
      {
        const pipeline = k.attention();
        const params = this.uniform(`${p}attention.params`, [
          c.heads,
          c.headDim,
          kvDim,
          c.heads / c.kvHeads,
          this.context,
          { f32: c.attentionScale },
        ]);
        add({
          label: `${p}attention`,
          pipeline,
          group: bindGroup(device, pipeline, {
            0: q,
            1: kCache,
            2: vCache,
            3: att,
            4: scores,
            5: params,
            6: this.step,
          }),
          workgroups: [c.heads, 1],
        });
      }
      matvec(await matrix(`${p}attn_output.weight`), att, o);
      elementwise(k.accumulate(), [x, o], c.embd, `${p}attn_residual`);

      rmsnorm(x, await vector(`${p}ffn_norm.weight`), h, `${p}ffn_norm`);
      matvec(await matrix(`${p}ffn_gate.weight`), h, gate);
      matvec(await matrix(`${p}ffn_up.weight`), h, up);
      elementwise(k.swiglu(), [gate, up, act], c.ffn, `${p}swiglu`);
      matvec(await matrix(`${p}ffn_down.weight`), act, o);
      elementwise(k.accumulate(), [x, o], c.embd, `${p}ffn_residual`);
    }

    rmsnorm(x, await vector('output_norm.weight'), h, 'output_norm');
    matvec(c.tiedEmbeddings ? embedding : await matrix('output.weight'), h, this.logits);

    // One trial step, inside the caller's error scope. It writes cache position 0,
    // which the first real step overwrites.
    this.queueStep(0, 0);
    await device.queue.onSubmittedWorkDone();
  }
}
