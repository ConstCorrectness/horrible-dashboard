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
    console.warn('Failed to load root .env file:', e);
  }
}

export default defineConfig({
  plugins: [react()],
  publicDir: resolve(__dirname, '../web/public'),
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/three') || id.includes('three/examples')) {
            return 'vendor-three';
          }
          if (id.includes('node_modules/@dimforge/rapier3d')) {
            return 'vendor-rapier';
          }
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor-react';
          }
        },
      },
    },
  },
  server: {
    host: process.env.HORRIBLE_DEV_HOST || '127.0.0.1',
    port: Number(process.env.ASSAULT_PORT) || 5180,
    strictPort: false,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        xfwd: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        ws: true,
      },
      '/hassault-ws': {
        target: `ws://127.0.0.1:${process.env.HORRIBLE_GAME_SERVER_PORT || '9200'}`,
        ws: true,
      },
    },
  },
});
