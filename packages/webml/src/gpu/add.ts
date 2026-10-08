/// <reference types="@webgpu/types" />
/** The `add` kernel (`wgsl/add.wgsl`): `out = a + b` over `n` f32s. */
import source from '../wgsl/add.wgsl?raw';
import { dispatch1d } from './dispatch';

const WORKGROUP_SIZE = 256;

export interface AddArgs {
  a: GPUBuffer;
  b: GPUBuffer;
  out: GPUBuffer;
  n: number;
}

export class AddKernel {
  private readonly pipeline: GPUComputePipeline;

  /**
   * @param maxPerDimension workgroups per dispatch dimension; defaults to the
   *   device's limit (a lower value is how tests reach the y-spill path).
   */
  constructor(
    private readonly device: GPUDevice,
    private readonly maxPerDimension = device.limits.maxComputeWorkgroupsPerDimension,
  ) {
    this.pipeline = device.createComputePipeline({
      label: 'add',
      layout: 'auto',
      compute: { module: device.createShaderModule({ label: 'add', code: source }) },
    });
  }

  /**
   * A uniform buffer and bind group per call: fine for a harness kernel. The 6.1
   * runtime builds these once at load and only rewrites a position buffer per token.
   */
  encode(encoder: GPUCommandEncoder, { a, b, out, n }: AddArgs): void {
    const params = this.device.createBuffer({
      label: 'add.params',
      size: 16,
      usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
    });
    this.device.queue.writeBuffer(params, 0, new Uint32Array([n, 0, 0, 0]));
    const bindGroup = this.device.createBindGroup({
      layout: this.pipeline.getBindGroupLayout(0),
      entries: [a, b, out, params].map((buffer, binding) => ({ binding, resource: { buffer } })),
    });
    const pass = encoder.beginComputePass({ label: 'add' });
    pass.setPipeline(this.pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.dispatchWorkgroups(...dispatch1d(n, WORKGROUP_SIZE, this.maxPerDimension));
    pass.end();
  }
}
