// Two test projects:
//
//   unit      `src/**/*.test.ts` in node: protocol, parser, planning — anything pure.
//   kernels   `kernel-tests/**/*.test.ts` in headless Chromium, WebGPU on SwiftShader
//             (Vulkan on the CPU), so every WGSL kernel runs in CI with no GPU.
//
// SwiftShader has `subgroups` and `timestamp-query` but not `shader-f16`, so the
// kernels project always exercises the f32 baseline path.
//
// `WEBML_HF_HEADER=<repo>/<file.gguf>` additionally reads that file's header from
// the Hub over Range requests (needs network; off by default).
import { playwright } from '@vitest/browser-playwright';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: 'unit',
          include: ['src/**/*.test.ts'],
          environment: 'node',
        },
      },
      {
        test: {
          name: 'kernels',
          include: ['kernel-tests/**/*.test.ts'],
          provide: { hfHeader: process.env.WEBML_HF_HEADER ?? '' },
          testTimeout: 30_000,
          browser: {
            enabled: true,
            headless: true,
            provider: playwright({
              launchOptions: {
                args: ['--enable-unsafe-webgpu', '--use-webgpu-adapter=swiftshader'],
              },
            }),
            instances: [{ browser: 'chromium' }],
          },
        },
      },
    ],
  },
});
