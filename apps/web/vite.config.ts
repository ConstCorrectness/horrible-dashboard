import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    // Honor a PORT assigned by the harness (preview autoPort); default to 5173 for `pnpm dev`.
    port: Number(process.env.PORT) || 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
});
