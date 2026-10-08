import { describe, expect, it } from 'vitest';

import { maxBindingBytes } from '../src/gpu/device';
import { gpu } from './harness';

describe('requestWebmlDevice', () => {
  it("is granted the adapter's limits, not WebGPU's defaults", async () => {
    const adapter = await navigator.gpu.requestAdapter();
    const { device, limits } = await gpu();
    expect(limits.maxStorageBufferBindingSize).toBe(adapter!.limits.maxStorageBufferBindingSize);
    expect(limits.maxBufferSize).toBe(adapter!.limits.maxBufferSize);
    expect(device.limits.maxComputeWorkgroupStorageSize).toBe(
      adapter!.limits.maxComputeWorkgroupStorageSize,
    );
    expect(maxBindingBytes(limits)).toBeGreaterThan(0);
  });

  it('reports exactly the optional features it was granted', async () => {
    const d = await gpu();
    expect(d.f16).toBe(d.device.features.has('shader-f16'));
    expect(d.subgroups).toBe(d.device.features.has('subgroups'));
    expect(d.timestamps).toBe(d.device.features.has('timestamp-query'));
  });
});
