import react from '@vitejs/plugin-react';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Load workspace root .env if running directly without scripts/dev.mjs wrapper
const rootEnv = resolve(__dirname, '../../.env');
if (existsSync(rootEnv)) {
  try {
    process.loadEnvFile(rootEnv);
  } catch (e) {
    console.warn('⚠️ Failed to load root .env file:', e);
  }
}

export default defineConfig({
  plugins: [react()],
  server: {
    // Listen host: `pnpm dev:lan` sets HORRIBLE_DEV_HOST=0.0.0.0 to expose the UI on
    // the LAN (peer-fabric collaboration); plain `pnpm dev` stays on localhost.
    host: process.env.HORRIBLE_DEV_HOST || '127.0.0.1',
    // Honor a PORT assigned by the harness (preview autoPort); default to 5173 for `pnpm dev`.
    port: Number(process.env.PORT) || 5173,
    strictPort: true,
    proxy: {
      // Backend port matches scripts/dev.mjs (HORRIBLE_DEV_BACKEND_PORT sidesteps
      // Windows' Hyper-V port-exclusion ranges when they swallow 8000).
      '/api': `http://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
      '/ws': {
        target: `ws://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        ws: true,
      },
    },
  },
});
