/**
 * One classic (IIFE) script, not an ES module: a scene frame is a sandboxed srcdoc
 * iframe with an opaque origin, and a module script from another origin needs CORS
 * headers the frame's `null` origin would have to be granted. A classic
 * `<script src>` needs none.
 *
 * Written into the backend package so it ships with the backend (the desktop build
 * bundles `backend/`), and served at `/api/scrive/runtime.js`.
 */
import { resolve } from 'node:path';

import { defineConfig } from 'vite';

export default defineConfig({
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  esbuild: { jsx: 'automatic' },
  build: {
    outDir: resolve(__dirname, '../../backend/modules/scrive/static'),
    emptyOutDir: false,
    target: 'es2022',
    sourcemap: false,
    minify: 'esbuild',
    lib: {
      entry: resolve(__dirname, 'src/runtime.tsx'),
      name: 'ScriveRuntime',
      formats: ['iife'],
      fileName: () => 'scrive-runtime.js',
    },
  },
});
