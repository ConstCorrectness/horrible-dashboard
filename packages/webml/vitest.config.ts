// Two test projects:
//
//   unit      `src/**/*.test.ts` in node: protocol, parser, planning — anything pure.
//   kernels   `kernel-tests/**/*.test.ts` in headless Chromium, WebGPU on SwiftShader
//             (Vulkan on the CPU), so every WGSL kernel runs in CI with no GPU.
//
// SwiftShader has `subgroups` and `timestamp-query` but not `shader-f16`, so the
// kernels project always exercises the f32 baseline path.
//
// WEBML_GPU=hardware runs the kernels project on the real GPU instead (the fast
// paths SwiftShader lacks, and the 6.4 speed measurements), in the installed Edge
// unless WEBML_BROWSER names another Playwright channel (`chrome`).
//
// Opt-in checks that need files or network (off by default):
//   WEBML_HF_HEADER=<repo>/<file.gguf>   read that file's header from the Hub
//   WEBML_PARITY_MODEL=<model.gguf> WEBML_PARITY_EXPECTED=<reference.json>
//                                        real-model parity (kernel-tests/real-parity.test.ts)
//   WEBML_BENCH_MODEL=<model.gguf> [WEBML_BENCH_TAG, WEBML_BENCH_SYSTEM]
//                                        speed measurements (kernel-tests/bench.test.ts)
//   WEBML_PERF=1 [WEBML_BENCH_TAG]       kernel timings at real shapes (kernel-tests/perf.test.ts)
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { playwright } from '@vitest/browser-playwright';
import { defineConfig } from 'vitest/config';

/** An absolute path from an env var, with `~` expanded; '' when unset. */
function envPath(name: string): string {
  const raw = process.env[name];
  if (!raw) return '';
  return resolve(raw.startsWith('~/') ? homedir() + raw.slice(1) : raw);
}

/** WEBML_GPU=hardware: run on this machine's GPU instead of SwiftShader. */
const hardware = process.env.WEBML_GPU === 'hardware';
const parityModel = envPath('WEBML_PARITY_MODEL');
const parityExpected = envPath('WEBML_PARITY_EXPECTED');
const benchModel = envPath('WEBML_BENCH_MODEL');

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
        // The parity files usually live outside the repo; let the browser project's
        // server hand them out via /@fs/.
        server: {
          fs: {
            allow: [
              fileURLToPath(new URL('../..', import.meta.url)),
              ...[parityModel, parityExpected, benchModel].filter(Boolean).map((p) => dirname(p)),
            ],
          },
        },
        test: {
          name: 'kernels',
          include: ['kernel-tests/**/*.test.ts'],
          provide: {
            hfHeader: process.env.WEBML_HF_HEADER ?? '',
            parityModel,
            parityExpected,
            benchModel,
            benchTag: process.env.WEBML_BENCH_TAG ?? 'run',
            benchSystem: Number(process.env.WEBML_BENCH_SYSTEM ?? 3500),
            perf: process.env.WEBML_PERF === '1',
            perfOnly: process.env.WEBML_PERF_ONLY ?? '',
          },
          // SwiftShader compiles each pipeline on the CPU, while the files run side by
          // side: a file's first test can take tens of seconds there.
          testTimeout: 120_000,
          browser: {
            enabled: true,
            headless: true,
            provider: playwright({
              launchOptions: {
                // The full Chromium build, not the headless shell: on Windows the
                // shell finds no WebGPU adapter at all. Hardware runs use an installed
                // Edge (or Chrome): Playwright's Chromium ships without dxil.dll, so
                // D3D12 device creation fails in it.
                channel: hardware ? (process.env.WEBML_BROWSER ?? 'msedge') : 'chromium',
                args: [
                  '--enable-unsafe-webgpu',
                  // Unquantized timestamp queries, for the per-kernel profile.
                  '--enable-webgpu-developer-features',
                  ...(hardware ? [] : ['--use-webgpu-adapter=swiftshader']),
                ],
              },
            }),
            instances: [{ browser: 'chromium' }],
          },
        },
      },
    ],
  },
});
