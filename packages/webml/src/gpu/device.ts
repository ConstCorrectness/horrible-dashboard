/// <reference types="@webgpu/types" />
/**
 * The GGUF engine's GPU device, asked for everything the adapter offers.
 *
 * `requestDevice()` with no limits gets WebGPU's defaults: 128 MiB per storage
 * binding, 256 MiB per buffer. A 152k-vocabulary embedding is larger than that on
 * its own, so the device is requested with the adapter's own maxima, and tensors
 * that still exceed them are split by rows (`chunks.ts`).
 */

/** The limits the engine sizes buffers and dispatches from. */
const WANTED_LIMITS = [
  'maxBufferSize',
  'maxStorageBufferBindingSize',
  'maxStorageBuffersPerShaderStage',
  'maxComputeWorkgroupStorageSize',
  'maxComputeInvocationsPerWorkgroup',
  'maxComputeWorkgroupSizeX',
  'maxComputeWorkgroupsPerDimension',
] as const;

export type WebmlLimits = Record<(typeof WANTED_LIMITS)[number], number>;

export interface WebmlDevice {
  device: GPUDevice;
  /** What the device was actually granted. */
  limits: WebmlLimits;
  /** `shader-f16`: f16 storage and arithmetic in WGSL. */
  f16: boolean;
  /** `subgroups`: subgroup reductions in WGSL. */
  subgroups: boolean;
  /** `timestamp-query`: per-pass GPU timings. */
  timestamps: boolean;
}

/** The optional features worth having; each is used only when granted. */
const OPTIONAL_FEATURES = ['shader-f16', 'subgroups', 'timestamp-query'] as const;

export async function requestWebmlDevice(adapter?: GPUAdapter): Promise<WebmlDevice> {
  if (!adapter) {
    const gpu = (globalThis.navigator as Navigator | undefined)?.gpu;
    if (!gpu) throw new Error('this browser has no WebGPU (navigator.gpu)');
    const found = await gpu.requestAdapter({ powerPreference: 'high-performance' });
    if (!found) throw new Error('no GPU adapter (blocklisted or disabled)');
    adapter = found;
  }
  const requiredLimits: Record<string, number> = {};
  for (const key of WANTED_LIMITS) requiredLimits[key] = adapter.limits[key];
  const requiredFeatures = OPTIONAL_FEATURES.filter((f) => adapter.features.has(f));

  const device = await adapter.requestDevice({
    requiredLimits,
    requiredFeatures: requiredFeatures as GPUFeatureName[],
  });
  const limits = {} as WebmlLimits;
  for (const key of WANTED_LIMITS) limits[key] = device.limits[key];
  return {
    device,
    limits,
    f16: device.features.has('shader-f16'),
    subgroups: device.features.has('subgroups'),
    timestamps: device.features.has('timestamp-query'),
  };
}

/** The largest buffer one storage binding can see: the smaller of the two limits. */
export function maxBindingBytes(limits: WebmlLimits): number {
  return Math.min(limits.maxBufferSize, limits.maxStorageBufferBindingSize);
}
