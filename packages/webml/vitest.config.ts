// Two test projects:
//
//   unit      `src/**/*.test.ts` in node: protocol, parser, planning — anything pure.
//   kernels   `kernel-tests/**/*.test.ts` in headless Chromium, WebGPU on SwiftShader
//             (Vulkan on the CPU), so every WGSL kernel runs in CI with no GPU.
//
// SwiftShader has `subgroups` and `timestamp-query` but not `shader-f16`, so the
// kernels project always exercises the f32 baseline path.
//
// Opt-in checks that need files or network (off by default):
//   WEBML_HF_HEADER=<repo>/<file.gguf>   read that file's header from the Hub
//   WEBML_PARITY_MODEL=<model.gguf> WEBML_PARITY_EXPECTED=<reference.json>
//                                        real-model parity (kernel-tests/real-parity.test.ts)
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

const parityModel = envPath('WEBML_PARITY_MODEL');
const parityExpected = envPath('WEBML_PARITY_EXPECTED');

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
              ...[parityModel, parityExpected].filter(Boolean).map((p) => dirname(p)),
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
          },
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
