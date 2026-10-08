/// <reference types="@webgpu/types" />
/** Shared plumbing for kernel tests: one device, upload, readback, seeded inputs. */
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
