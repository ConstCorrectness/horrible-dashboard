import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import { horribleModules } from '@horribledashboard/sdk/vite-modules';

/**
 * The module shell: one module package full-window, talking to the slim backend
 * `horrible run <id>` started (backend/cli.py passes its port in
 * HORRIBLE_DEV_BACKEND_PORT). Every module package is in the bundle, code-split, so
 * one build serves any `?module=<id>`.
 */
const backend = process.env.HORRIBLE_DEV_BACKEND_PORT || '8000';

export default defineConfig({
  plugins: [react(), horribleModules({ root: resolve(__dirname, '../../modules') })],
  // The dashboard's public dir: icons and anything a pane loads by URL.
  publicDir: resolve(__dirname, '../web/public'),
  server: {
    host: process.env.HORRIBLE_DEV_HOST || '127.0.0.1',
    port: Number(process.env.MODULE_SHELL_PORT) || 5190,
    strictPort: true,
    proxy: {
      '/api': { target: `http://127.0.0.1:${backend}`, xfwd: true },
      '/ws': { target: `ws://127.0.0.1:${backend}`, ws: true },
    },
  },
});
