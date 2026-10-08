/// <reference types="@webgpu/types" />
/**
 * What this browser's GPU can do, read once. WebGPU needs a secure context
 * (https, localhost, `*.localhost`, the desktop app's own origin) and a supported
 * adapter; anything short of that is reported with its reason rather than thrown.
 */

export interface WebGpuReport {
  available: true;
  vendor: string;
  architecture: string;
  description: string;
  /** `shader-f16` — required by the q4f16 weight variants. */
  f16: boolean;
  subgroups: boolean;
  timestampQuery: boolean;
  maxBufferSize: number;
  maxStorageBufferBindingSize: number;
  /** A fallback (software) adapter: it works, slowly. */
  isFallback: boolean;
}

export interface WebGpuUnavailable {
  available: false;
  reason: string;
}

export type GpuReport = WebGpuReport | WebGpuUnavailable;

let cached: Promise<GpuReport> | null = null;

/** The report, probed on first call and shared after. */
export function probeWebGpu(): Promise<GpuReport> {
  cached ??= probe();
  return cached;
}

async function probe(): Promise<GpuReport> {
  if (typeof globalThis.isSecureContext === 'boolean' && !globalThis.isSecureContext) {
    return { available: false, reason: 'not a secure context (WebGPU needs https or localhost)' };
  }
  const gpu = (globalThis.navigator as Navigator | undefined)?.gpu;
  if (!gpu) return { available: false, reason: 'this browser has no WebGPU (navigator.gpu)' };
  let adapter: GPUAdapter | null;
  try {
    adapter = await gpu.requestAdapter({ powerPreference: 'high-performance' });
  } catch (err) {
    return { available: false, reason: `requestAdapter failed: ${String(err)}` };
  }
  if (!adapter) return { available: false, reason: 'no GPU adapter (blocklisted or disabled)' };

  // `adapter.info` is the current API; older engines only had `requestAdapterInfo()`.
  const info: Partial<GPUAdapterInfo> =
    adapter.info ??
    (await (
      adapter as unknown as { requestAdapterInfo?: () => Promise<GPUAdapterInfo> }
    ).requestAdapterInfo?.()) ??
    {};
  const has = (feature: string) => adapter.features.has(feature as GPUFeatureName);
  return {
    available: true,
    vendor: info.vendor ?? '',
    architecture: info.architecture ?? '',
    description: info.description ?? '',
    f16: has('shader-f16'),
    subgroups: has('subgroups'),
    timestampQuery: has('timestamp-query'),
    maxBufferSize: adapter.limits.maxBufferSize,
    maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
    isFallback:
      (info as { isFallbackAdapter?: boolean }).isFallbackAdapter ??
      (adapter as unknown as { isFallbackAdapter?: boolean }).isFallbackAdapter ??
      false,
  };
}

/** Test seam: forget the cached report. */
export function resetProbe(): void {
  cached = null;
}
