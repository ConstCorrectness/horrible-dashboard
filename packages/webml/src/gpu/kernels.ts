/// <reference types="@webgpu/types" />
/**
 * The engine's compute pipelines, compiled once per device and cached.
 *
 * `matvec` and `embed` read weights in their ggml block layout, so each is
 * composed per weight type: common.wgsl (byte reads) + weights/<type>.wgsl
 * (BLOCK, BLOCK_BYTES, block_dot, block_get) + the kernel body.
 */
import { GGML_TYPES } from '../gguf/ggml';
import { F16, F32, Q4_0, Q8_0 } from '../gguf/quant';
import accumulateSrc from '../wgsl/accumulate.wgsl?raw';
import attentionSrc from '../wgsl/attention.wgsl?raw';
import commonSrc from '../wgsl/common.wgsl?raw';
import embedSrc from '../wgsl/embed.wgsl?raw';
import matvecSrc from '../wgsl/matvec.wgsl?raw';
import rmsnormSrc from '../wgsl/rmsnorm.wgsl?raw';
import ropeSrc from '../wgsl/rope.wgsl?raw';
import swigluSrc from '../wgsl/swiglu.wgsl?raw';
import f16Src from '../wgsl/weights/f16.wgsl?raw';
import f32Src from '../wgsl/weights/f32.wgsl?raw';
import q4_0Src from '../wgsl/weights/q4_0.wgsl?raw';
import q8_0Src from '../wgsl/weights/q8_0.wgsl?raw';

/**
 * How the kernels step through a row of each weight type: values and bytes per
 * step. The ggml block for quants; 4 (F32) or 2 (F16) values per word-aligned read
 * for floats, so their rows must be a multiple of that.
 */
export const KERNEL_BLOCK: Readonly<Record<number, { elems: number; bytes: number }>> = {
  [F32]: { elems: 4, bytes: 16 },
  [F16]: { elems: 2, bytes: 4 },
  [Q8_0]: { elems: 32, bytes: 34 },
  [Q4_0]: { elems: 32, bytes: 18 },
};

const WEIGHT_SNIPPETS: Record<number, string> = {
  [F32]: f32Src,
  [F16]: f16Src,
  [Q8_0]: q8_0Src,
  [Q4_0]: q4_0Src,
};

/** Workgroup sizes, matching the `WG` constants in the shaders. */
export const WG = {
  matvec: 64,
  embed: 256,
  rmsnorm: 256,
  rope: 64,
  attention: 256,
  swiglu: 256,
  accumulate: 256,
} as const;

export class Kernels {
  private readonly cache = new Map<string, GPUComputePipeline>();

  constructor(readonly device: GPUDevice) {}

  matvec(type: number): GPUComputePipeline {
    return this.get(`matvec.${type}`, () => commonSrc + weightSnippet(type) + matvecSrc);
  }

  embed(type: number): GPUComputePipeline {
    return this.get(`embed.${type}`, () => commonSrc + weightSnippet(type) + embedSrc);
  }

  rmsnorm = () => this.get('rmsnorm', () => rmsnormSrc);
  rope = () => this.get('rope', () => ropeSrc);
  attention = () => this.get('attention', () => attentionSrc);
  swiglu = () => this.get('swiglu', () => swigluSrc);
  accumulate = () => this.get('accumulate', () => accumulateSrc);

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
