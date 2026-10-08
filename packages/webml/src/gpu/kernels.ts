/// <reference types="@webgpu/types" />
// Packages that compile this source with their own tsconfig (@horrible/core) need the
// `?raw` declaration to come with it; an import cannot carry an ambient module.
// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../wgsl/wgsl.d.ts" />
/**
 * The engine's compute pipelines, compiled once per device and cached.
 *
 * `matvec`, `matmul` and `embed` read weights in their ggml block layout, so
 * each is composed per weight type: common.wgsl (byte reads) + weights/<type>.wgsl
 * (BLOCK, BLOCK_BYTES, block_dot, block_get) + the kernel body.
 */
import { GGML_TYPES } from '../gguf/ggml';
import { F16, F32, Q4_0, Q4_K, Q5_0, Q5_K, Q6_K, Q8_0 } from '../gguf/quant';
import attnCombineSrc from '../wgsl/attn_combine.wgsl?raw';
import attnPrefillSrc from '../wgsl/attn_prefill.wgsl?raw';
import attentionSrc from '../wgsl/attention.wgsl?raw';
import biasSrc from '../wgsl/bias.wgsl?raw';
import commonSrc from '../wgsl/common.wgsl?raw';
import embedSrc from '../wgsl/embed.wgsl?raw';
import gluSrc from '../wgsl/glu.wgsl?raw';
import headnormSrc from '../wgsl/headnorm.wgsl?raw';
import kvF16Src from '../wgsl/kv_f16.wgsl?raw';
import kvF16PackSrc from '../wgsl/kv_f16_pack.wgsl?raw';
import kvF32Src from '../wgsl/kv_f32.wgsl?raw';
import kvStoreSrc from '../wgsl/kv_store.wgsl?raw';
import lensInSrc from '../wgsl/lens_in.wgsl?raw';
import lensTopkSrc from '../wgsl/lens_topk.wgsl?raw';
import matmulSrc from '../wgsl/matmul.wgsl?raw';
import matvecSrc from '../wgsl/matvec.wgsl?raw';
import rmsnormSrc from '../wgsl/rmsnorm.wgsl?raw';
import ropeSrc from '../wgsl/rope.wgsl?raw';
import sampleSrc from '../wgsl/sample.wgsl?raw';
import f16Src from '../wgsl/weights/f16.wgsl?raw';
import f32Src from '../wgsl/weights/f32.wgsl?raw';
import kscaleSrc from '../wgsl/weights/kscale.wgsl?raw';
import q4_0Src from '../wgsl/weights/q4_0.wgsl?raw';
import q4_kSrc from '../wgsl/weights/q4_k.wgsl?raw';
import q5_0Src from '../wgsl/weights/q5_0.wgsl?raw';
import q5_kSrc from '../wgsl/weights/q5_k.wgsl?raw';
import q6_kSrc from '../wgsl/weights/q6_k.wgsl?raw';
import q8_0Src from '../wgsl/weights/q8_0.wgsl?raw';

/**
 * How the kernels step through a row of each weight type, matching the UNIT /
 * UNITS / BLOCK_BYTES constants of its weights/<type>.wgsl: `unit` values per
 * piece of work, `units` per block of `bytes`. A row must be whole blocks: 32
 * values for F32, F16, Q8_0, Q4_0 and Q5_0, 256 for the K-quants (eight units of 32).
 */
export const KERNEL_BLOCK: Readonly<
  Record<number, { unit: number; units: number; bytes: number }>
> = {
  [F32]: { unit: 32, units: 1, bytes: 128 },
  [F16]: { unit: 32, units: 1, bytes: 64 },
  [Q8_0]: { unit: 32, units: 1, bytes: 34 },
  [Q4_0]: { unit: 32, units: 1, bytes: 18 },
  [Q5_0]: { unit: 32, units: 1, bytes: 22 },
  [Q4_K]: { unit: 32, units: 8, bytes: 144 },
  [Q5_K]: { unit: 32, units: 8, bytes: 176 },
  [Q6_K]: { unit: 32, units: 8, bytes: 210 },
};

/** Values in one block of `type`: what a row's length must be a multiple of. */
export function blockValues(type: number): number {
  const b = KERNEL_BLOCK[type];
  return b.unit * b.units;
}

const WEIGHT_SNIPPETS: Record<number, string> = {
  [F32]: f32Src,
  [F16]: f16Src,
  [Q8_0]: q8_0Src,
  [Q4_0]: q4_0Src,
  [Q5_0]: q5_0Src,
  [Q4_K]: kscaleSrc + q4_kSrc,
  [Q5_K]: kscaleSrc + q5_kSrc,
  [Q6_K]: q6_kSrc,
};

/** The KV cache's element type: f16 halves its memory and what attention reads. */
export type KvType = 'f16' | 'f32';

const KV_SRC: Record<KvType, string> = { f16: kvF16Src, f32: kvF32Src };

/**
 * attention.wgsl's views of the cache: K in 16-byte pieces (KPV values, dotted
 * with the query in workgroup memory `qs`), V one dimension pair at a time.
 */
const ATTENTION_KV_SRC: Record<KvType, string> = {
  f16: [
    'alias KP = vec4<u32>;',
    'alias VW = u32;',
    'const KPV: u32 = 8u;',
    'fn kp_dot(at: u32, x: KP) -> f32 {',
    '  return dot(qs[at], vec4<f32>(unpack2x16float(x.x), unpack2x16float(x.y)))',
    '    + dot(qs[at + 1u], vec4<f32>(unpack2x16float(x.z), unpack2x16float(x.w)));',
    '}',
    'fn v_pair(x: VW) -> vec2<f32> {',
    '  return unpack2x16float(x);',
    '}',
  ].join('\n'),
  f32: [
    'alias KP = vec4<f32>;',
    'alias VW = vec2<f32>;',
    'const KPV: u32 = 4u;',
    'fn kp_dot(at: u32, x: KP) -> f32 {',
    '  return dot(qs[at], x);',
    '}',
    'fn v_pair(x: VW) -> vec2<f32> {',
    '  return x;',
    '}',
  ].join('\n'),
};

/** kv_store.wgsl's word type and writer, per cache type. */
const KV_STORE_SRC: Record<KvType, string> = {
  f16: [
    'alias CacheWord = u32;',
    'fn store(at: u32, k: vec2<f32>, v: vec2<f32>) {',
    '  KC[at] = f16_pair(k);',
    '  VC[at] = f16_pair(v);',
    '}',
    kvF16PackSrc,
  ].join('\n'),
  f32: [
    'alias CacheWord = vec2<f32>;',
    'fn store(at: u32, k: vec2<f32>, v: vec2<f32>) {',
    '  KC[at] = k;',
    '  VC[at] = v;',
    '}',
  ].join('\n'),
};

/** Workgroup sizes, matching the `WG` constants in the shaders. */
export const WG = {
  matvec: 64,
  /** Rows per matvec workgroup (matvec.wgsl's RPW). */
  matvecRows: 8,
  matmul: 64,
  attnPrefill: 64,
  embed: 256,
  rmsnorm: 256,
  headnorm: 64,
  rope: 64,
  attention: 128,
  attnCombine: 128,
  glu: 256,
  bias: 256,
  kvStore: 256,
} as const;

/** Positions per decode-attention chunk (attention.wgsl's CH): one workgroup each. */
export const ATTN_CHUNK = 128;

/** The largest head attention.wgsl holds in workgroup memory. */
export const MAX_HEAD_DIM = 256;

/** The most query heads per KV head attention.wgsl serves together. */
export const MAX_GQA_GROUP = 8;

/** Tokens the logit lens reads out per layer (lens_topk.wgsl's K). */
export const LENS_K = 5;

/** Words per layer in a lens readout: norm, entropy, then (id, p) per token. */
export const LENS_STRIDE = 2 + 2 * LENS_K;

/** The output tile of one matmul workgroup: weight rows × batch rows (matmul.wgsl). */
export const MATMUL_TILE = { rows: 64, batch: 32 } as const;

/**
 * Threads sharing one query in attn_prefill: 8, each owning every 8th vec4 of
 * the head, or 4 for heads of 16 or 48. Heads must be a multiple of 16.
 */
export function attnPrefillLanes(headDim: number): number {
  if (headDim % 32 === 0) return 8;
  if (headDim % 16 === 0) return 4;
  throw new Error(`attention heads of ${headDim} are not a multiple of 16`);
}

/** Queries per attn_prefill workgroup, for heads of `headDim`. */
export function attnPrefillTile(headDim: number): number {
  return WG.attnPrefill / attnPrefillLanes(headDim);
}

export class Kernels {
  private readonly cache = new Map<string, GPUComputePipeline>();

  constructor(readonly device: GPUDevice) {}

  matvec(type: number): GPUComputePipeline {
    return this.get(`matvec.${type}`, () => commonSrc + weightSnippet(type) + matvecSrc);
  }

  matmul(type: number): GPUComputePipeline {
    return this.get(`matmul.${type}`, () => commonSrc + weightSnippet(type) + matmulSrc);
  }

  embed(type: number): GPUComputePipeline {
    return this.get(`embed.${type}`, () => commonSrc + weightSnippet(type) + embedSrc);
  }

  /** Prefill attention for heads of `headDim`, `attnPrefillTile(headDim)` queries per workgroup. */
  attnPrefill(headDim: number, kv: KvType): GPUComputePipeline {
    const lanes = attnPrefillLanes(headDim);
    return this.get(
      `attn_prefill.${headDim}.${kv}`,
      () =>
        [`const HD: u32 = ${headDim}u;`, `const LANES: u32 = ${lanes}u;`, KV_SRC[kv]].join('\n') +
        '\n' +
        attnPrefillSrc,
    );
  }

  /** Decode attention's split pass over a cache of `kv`, `group` query heads per KV head. */
  attention(kv: KvType, headDim: number, group: number): GPUComputePipeline {
    if (headDim % 4 || headDim > MAX_HEAD_DIM)
      throw new Error(`no attention for heads of ${headDim}`);
    if (group > MAX_GQA_GROUP) throw new Error(`no attention for ${group} query heads per KV head`);
    return this.get(`attention.${kv}.${headDim}.${group}`, () =>
      [
        `const HD: u32 = ${headDim}u;`,
        `const GROUP: u32 = ${group}u;`,
        ATTENTION_KV_SRC[kv],
        attentionSrc,
      ].join('\n'),
    );
  }

  /** New K and V rows into a cache of `kv`. */
  kvStore(kv: KvType): GPUComputePipeline {
    return this.get(`kv_store.${kv}`, () => KV_STORE_SRC[kv] + '\n' + kvStoreSrc);
  }

  rmsnorm = () => this.get('rmsnorm', () => rmsnormSrc);
  headnorm = () => this.get('headnorm', () => headnormSrc);
  rope = () => this.get('rope', () => ropeSrc);
  attnCombine = () => this.get('attn_combine', () => attnCombineSrc);
  glu = () => this.get('glu', () => gluSrc);
  bias = () => this.get('bias', () => biasSrc);
  sample = () => this.get('sample', () => sampleSrc);
  lensIn = () => this.get('lens_in', () => lensInSrc);
  lensTopk = () => this.get('lens_topk', () => lensTopkSrc);

  private get(key: string, source: () => string): GPUComputePipeline {
    let pipeline = this.cache.get(key);
    if (!pipeline) {
      pipeline = this.device.createComputePipeline({
        label: key,
        layout: 'auto',
        compute: { module: this.device.createShaderModule({ label: key, code: source() }) },
      });
      this.cache.set(key, pipeline);
    }
    return pipeline;
  }
}

function weightSnippet(type: number): string {
  const src = WEIGHT_SNIPPETS[type];
  if (!src) throw new Error(`no kernel for ${GGML_TYPES[type]?.name ?? `type ${type}`} weights`);
  return src;
}

/** A field of a uniform struct: u32 unless marked as f32. */
export type UniformField = number | { f32: number };

/** A uniform buffer holding `fields` in order (each 4 bytes), padded to 16 bytes. */
export function uniformBuffer(device: GPUDevice, label: string, fields: UniformField[]): GPUBuffer {
  const words = Math.max(4, Math.ceil(fields.length / 4) * 4);
  const data = new ArrayBuffer(words * 4);
  const view = new DataView(data);
  fields.forEach((f, i) => {
    if (typeof f === 'number') view.setUint32(i * 4, f, true);
    else view.setFloat32(i * 4, f.f32, true);
  });
  const buffer = device.createBuffer({
    label,
    size: data.byteLength,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(buffer, 0, data);
  return buffer;
}

/** A bind group for group 0 of `pipeline`, binding each buffer at its key. */
export function bindGroup(
  device: GPUDevice,
  pipeline: GPUComputePipeline,
  buffers: Record<number, GPUBuffer>,
  label?: string,
): GPUBindGroup {
  return device.createBindGroup({
    label,
    layout: pipeline.getBindGroupLayout(0),
    entries: Object.entries(buffers).map(([binding, buffer]) => ({
      binding: Number(binding),
      resource: { buffer },
    })),
  });
}
