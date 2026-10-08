/// <reference types="@webgpu/types" />
/** Shared plumbing for kernel tests: one device, upload, readback, seeded inputs. */
import { commands } from 'vitest/browser';

import { requestWebmlDevice, type WebmlDevice } from '../src/gpu/device';

let shared: Promise<WebmlDevice> | null = null;

/** The test run's device. Uncaptured GPU errors fail loudly instead of silently. */
export function gpu(): Promise<WebmlDevice> {
  shared ??= requestWebmlDevice().then((d) => {
    d.device.addEventListener('uncapturederror', (e) => {
      throw new Error(`uncaptured WebGPU error: ${(e as GPUUncapturedErrorEvent).error.message}`);
    });
    return d;
  });
  return shared;
}

export function upload(device: GPUDevice, data: Float32Array, label = 'input'): GPUBuffer {
  const buffer = device.createBuffer({
    label,
    size: Math.max(4, Math.ceil(data.byteLength / 4) * 4),
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST,
    mappedAtCreation: true,
  });
  new Float32Array(buffer.getMappedRange()).set(data);
  buffer.unmap();
  return buffer;
}

export function output(device: GPUDevice, floats: number, label = 'output'): GPUBuffer {
  return device.createBuffer({
    label,
    size: Math.max(4, floats * 4),
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
  });
}

/**
 * Submit `encode`'s commands, then copy `buffer` back. Validation errors raised by
 * the commands are surfaced as a rejection rather than as zeros in the result.
 */
export async function run(
  device: GPUDevice,
  encode: (encoder: GPUCommandEncoder) => void,
  buffer: GPUBuffer,
  floats: number,
): Promise<Float32Array> {
  device.pushErrorScope('validation');
  const encoder = device.createCommandEncoder();
  encode(encoder);
  const staging = device.createBuffer({
    size: floats * 4,
    usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
  });
  encoder.copyBufferToBuffer(buffer, 0, staging, 0, floats * 4);
  device.queue.submit([encoder.finish()]);
  const error = await device.popErrorScope();
  if (error) throw new Error(`WebGPU validation: ${error.message}`);
  await staging.mapAsync(GPUMapMode.READ);
  const out = new Float32Array(staging.getMappedRange().slice(0));
  staging.destroy();
  return out;
}

/** Deterministic floats in [-1, 1): a failing test reproduces exactly. */
export function seeded(n: number, seed: number): Float32Array {
  let s = seed >>> 0 || 1;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    // xorshift32
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    out[i] = ((s >>> 0) / 2 ** 32) * 2 - 1;
  }
  return out;
}

/** f32 → IEEE half bits, round-half-up, flushing values below the normal range to 0. */
export function f32ToF16(v: number): number {
  const u = new Uint32Array(new Float32Array([v]).buffer)[0];
  const sign = (u >>> 16) & 0x8000;
  const exp = ((u >>> 23) & 0xff) - 127 + 15;
  const mant = u & 0x7fffff;
  if (exp <= 0) return sign;
  if (exp >= 31) return sign | 0x7c00;
  return (sign | (exp << 10) | (mant >> 13)) + ((mant >> 12) & 1);
}

/** f32 → IEEE half bits, rounding to nearest even (as pack2x16float), subnormals kept. */
export function f16Bits(v: number): number {
  const u = new Uint32Array(new Float32Array([v]).buffer)[0];
  const sign = (u >>> 16) & 0x8000;
  const exp = (u >>> 23) & 0xff;
  let mant = u & 0x7fffff;
  if (exp === 0xff) return sign | 0x7c00 | (mant ? 0x200 : 0);
  const e = exp - 127 + 15;
  if (e >= 31) return sign | 0x7c00;
  let shift = 13;
  let base = e << 10;
  if (e <= 0) {
    if (e < -10) return sign;
    mant |= 0x800000;
    shift = 14 - e;
    base = 0;
  }
  let half = base | (mant >>> shift);
  const rem = mant & ((1 << shift) - 1);
  const mid = 1 << (shift - 1);
  // A carry out of the mantissa correctly bumps the exponent.
  if (rem > mid || (rem === mid && half & 1)) half++;
  return sign | half;
}

/** IEEE half bits → number. */
export function fromF16Bits(h: number): number {
  const sign = h & 0x8000 ? -1 : 1;
  const exp = (h >>> 10) & 0x1f;
  const mant = h & 0x3ff;
  if (exp === 0) return sign * mant * 2 ** -24;
  if (exp === 31) return mant ? NaN : sign * Infinity;
  return sign * (1 + mant / 1024) * 2 ** (exp - 15);
}

/** `values` rounded to f16: the packed halves, and their values as f32. */
export function toF16(values: ArrayLike<number>): { bits: Uint16Array; values: Float32Array } {
  const bits = Uint16Array.from(values as ArrayLike<number>, f16Bits);
  return { bits, values: Float32Array.from(bits, fromF16Bits) };
}

/**
 * `rows` rows of `cols` values in ggml layout for `type`, from seeded noise: random
 * quant bytes with sane f16 scales (random bytes would include NaN/Inf scales),
 * random f16 / f32 values for the float types.
 */
export function randomWeights(type: number, rows: number, cols: number, seed: number): Uint8Array {
  const noise = seeded(rows * cols * 2, seed);
  let n = 0;
  const next = () => noise[n++ % noise.length];
  // [values, bytes] per block, and where the block's f16 scales sit.
  const sizes: Record<number, [number, number, number[]]> = {
    0: [1, 4, []],
    1: [1, 2, []],
    8: [32, 34, [0]],
    2: [32, 18, [0]],
    6: [32, 22, [0]],
    12: [256, 144, [0, 2]],
    13: [256, 176, [0, 2]],
    14: [256, 210, [208]],
  };
  const [elems, bytesPer, scales] = sizes[type];
  const out = new Uint8Array((rows * cols * bytesPer) / elems);
  const view = new DataView(out.buffer);
  if (type === 0) {
    for (let i = 0; i < rows * cols; i++) view.setFloat32(4 * i, next(), true);
  } else if (type === 1) {
    for (let i = 0; i < rows * cols; i++) view.setUint16(2 * i, f32ToF16(next()), true);
  } else {
    for (let i = 0; i < out.length; i++) out[i] = Math.floor(((next() + 1) / 2) * 256) & 255;
    for (let b = 0; b < out.length / bytesPer; b++) {
      for (const at of scales) {
        view.setUint16(b * bytesPer + at, f32ToF16(0.002 + 0.01 * Math.abs(next())), true);
      }
    }
  }
  return out;
}

export function uploadBytes(device: GPUDevice, bytes: Uint8Array, label = 'weights'): GPUBuffer {
  const buffer = device.createBuffer({
    label,
    size: Math.max(4, Math.ceil(bytes.byteLength / 4) * 4),
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
    mappedAtCreation: true,
  });
  new Uint8Array(buffer.getMappedRange()).set(bytes);
  buffer.unmap();
  return buffer;
}

/** The Step uniform the kernels read: the pass's first position and its row count. */
export function stepBuffer(device: GPUDevice, pos: number, n = 1): GPUBuffer {
  const buffer = device.createBuffer({
    size: 16,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(buffer, 0, new Uint32Array([pos, n, 0, 0]));
  return buffer;
}

/** Token ids as the u32 storage buffer `embed` reads. */
export function tokenBuffer(device: GPUDevice, ids: number[]): GPUBuffer {
  const buffer = device.createBuffer({
    size: Math.max(16, ids.length * 4),
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(buffer, 0, Uint32Array.from(ids));
  return buffer;
}

/** Run one dispatch of `pipeline` and read back `floats` of `out`. */
export function dispatchOnce(
  device: GPUDevice,
  pipeline: GPUComputePipeline,
  bindings: Record<number, GPUBuffer>,
  workgroups: [number, number],
  out: GPUBuffer,
  floats: number,
): Promise<Float32Array> {
  return run(
    device,
    (enc) => {
      const pass = enc.beginComputePass();
      pass.setPipeline(pipeline);
      pass.setBindGroup(
        0,
        device.createBindGroup({
          layout: pipeline.getBindGroupLayout(0),
          entries: Object.entries(bindings).map(([b, buffer]) => ({
            binding: Number(b),
            resource: { buffer },
          })),
        }),
      );
      pass.dispatchWorkgroups(...workgroups);
      pass.end();
    },
    out,
    floats,
  );
}

/** Max |a - b| over `scale` (elementwise), the comparison every kernel test uses. */
export function maxRelError(
  got: ArrayLike<number>,
  want: ArrayLike<number>,
  scale: ArrayLike<number>,
): number {
  let worst = 0;
  for (let i = 0; i < want.length; i++) {
    worst = Math.max(worst, Math.abs(got[i] - want[i]) / Math.max(scale[i], 1e-12));
  }
  return worst;
}

/**
 * A fetch that serves `file` like the Hub's CDN: honours `Range`, reports
 * `Content-Range`. With `cutAfter`, the first response body errors after that many
 * bytes (a dropped connection), and later ones are whole.
 */
export function fileServer(file: Uint8Array, options: { cutAfter?: number } = {}) {
  const requests: (string | null)[] = [];
  let cut = options.cutAfter;
  const fetcher = (async (_url: string, init?: RequestInit) => {
    const range = (init?.headers as Record<string, string> | undefined)?.Range ?? null;
    requests.push(range);
    const m = range ? /bytes=(\d+)-(\d*)/.exec(range) : null;
    const start = m ? Number(m[1]) : 0;
    const end = m && m[2] ? Math.min(Number(m[2]), file.length - 1) : file.length - 1;
    if (start >= file.length) return new Response(null, { status: 416 });
    const body = file.slice(start, end + 1);
    const limit = cut;
    cut = undefined;
    let sent = false;
    // Pull-based, so the cut arrives after the reader has taken the bytes before it
    // (erroring in `start` would discard what was queued).
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent) {
          if (limit === undefined) controller.close();
          else controller.error(new Error('connection reset'));
          return;
        }
        sent = true;
        controller.enqueue(limit === undefined ? body : body.slice(0, limit));
      },
    });
    return new Response(stream, {
      status: m ? 206 : 200,
      headers: m
        ? { 'content-range': `bytes ${start}-${end}/${file.length}` }
        : { 'content-length': String(file.length) },
    });
  }) as typeof fetch;
  return { fetcher, requests };
}

/** A fixture file's bytes, by its `?url`. */
export async function fixtureBytes(url: string): Promise<Uint8Array> {
  return new Uint8Array(await (await fetch(url)).arrayBuffer());
}

/**
 * Write a measurement to `kernel-tests/.results/<name>.json` (gitignored). Browser
 * mode does not forward the page's console here, so numbers worth keeping — parity
 * summaries, benchmark timings — go to a file instead.
 */
export async function report(name: string, data: unknown): Promise<void> {
  await commands.writeFile(
    `kernel-tests/.results/${name}.json`,
    JSON.stringify(data, null, 2) + '\n',
  );
}

/**
 * A local file through Vite's `/@fs/`, for POSIX and Windows paths alike. Its
 * directory must be in the kernels project's `server.fs.allow`.
 */
export async function fetchLocal(path: string): Promise<Response> {
  const url = '/@fs/' + path.replace(/\\/g, '/').replace(/^\/+/, '');
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res;
}
