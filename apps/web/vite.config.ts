import react from '@vitejs/plugin-react';
import { existsSync } from 'node:fs';
import { Agent } from 'node:http';
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
  optimizeDeps: {
    include: [
      '@codemirror/state',
      '@codemirror/view',
      '@codemirror/commands',
      '@codemirror/language',
      '@codemirror/autocomplete',
      '@codemirror/search',
      '@codemirror/lint',
      '@codemirror/theme-one-dark',
      '@codemirror/lang-python',
      '@codemirror/lang-markdown',
      '@codemirror/lang-javascript',
      'codemirror',
      '@mui/material',
      '@emotion/react',
      '@emotion/styled',
    ],
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vite's `__vitePreload` (and Rollup's CJS interop) are shared by every
          // dynamic import. Left unassigned, Rollup hoists them into whichever
          // manual chunk used them first — CodeMirror, via language-data — and the
          // entry then has to load that whole 1.7 MB chunk to call a 1 KB helper.
          if (id.includes('vite/preload-helper') || id.includes('commonjsHelpers')) {
            return 'runtime';
          }
          if (id.includes('node_modules/three') || id.includes('three/examples')) {
            return 'vendor-three';
          }
          if (id.includes('node_modules/@dimforge/rapier3d')) {
            return 'vendor-rapier';
          }
          if (id.includes('node_modules/@mui') || id.includes('node_modules/@emotion')) {
            return 'vendor-mui';
          }
          if (
            id.includes('node_modules/codemirror') ||
            id.includes('node_modules/@codemirror') ||
            id.includes('node_modules/@lezer')
          ) {
            return 'vendor-codemirror';
          }
          if (id.includes('node_modules/@xterm')) {
            return 'vendor-xterm';
          }
          if (id.includes('node_modules/agora-rtc-sdk-ng')) {
            return 'vendor-agora';
          }
          if (id.includes('node_modules/pdfjs-dist')) {
            return 'vendor-pdfjs';
          }
          if (id.includes('node_modules/@xyflow')) {
            return 'vendor-xyflow';
          }
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor-react';
          }
        },
      },
    },
  },
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
      '/api': {
        target: `http://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        // Without an agent, http-proxy sends `Connection: close` upstream and relays
        // the backend's `Connection: close` back, so every API call opened a fresh
        // TCP connection to this server. On Windows a fresh connection to
        // `localhost` costs ~250ms (the IPv6 attempt is refused before the IPv4 one
        // is tried — we listen on 127.0.0.1 only), which put a quarter second of
        // dead time on every API request in the browser layout. (The desktop shell
        // calls the backend by absolute URL and never goes through this proxy.)
        agent: new Agent({ keepAlive: true }),
        // X-Forwarded-For, so the backend can tell a request this proxy relayed
        // from a LAN client (`pnpm dev:lan`) from one made on this machine. Without
        // it every proxied request looks like loopback -- see otel/auth.py.
        xfwd: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        ws: true,
      },
    },
  },
});
