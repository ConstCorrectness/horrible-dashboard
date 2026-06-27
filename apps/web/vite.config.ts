import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    // Listen host: `dev:all` sets HORRIBLE_DEV_HOST=0.0.0.0 to expose the UI on the
    // LAN (peer-fabric collaboration); plain `pnpm dev` stays on localhost.
    host: process.env.HORRIBLE_DEV_HOST || '127.0.0.1',
    // Honor a PORT assigned by the harness (preview autoPort); default to 5173 for `pnpm dev`.
    port: Number(process.env.PORT) || 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
});
