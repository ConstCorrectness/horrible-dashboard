/**
 * The GGUF engine for published Scrive pages: `gguf.worker.ts` and everything it
 * imports (the WGSL kernels, the tokenizer, the chat-template interpreter) as one
 * ES-module worker script, `webml-gguf.worker.js`.
 *
 * A published `{webllm}` with a `gguf:` model is an iframe of
 * `_scrive/webml/embed.html` on the blog's own origin; the page starts this file
 * beside it as a module worker and speaks the same protocol the app's
 * `WebmlEngine` does (src/protocol.ts). Only the worker is bundled — the page
 * drives it in a few lines of plain JS, so the app's client and the
 * transformers.js worker stay out of the file.
 *
 * Written into the backend package, gitignored, like the scene runtime: the
 * desktop build bundles `backend/`, and publishing copies it into a site that
 * needs it.
 */
import { resolve } from 'node:path';

import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    outDir: resolve(__dirname, '../../backend/modules/scrive/static'),
    emptyOutDir: false,
    target: 'es2022',
    sourcemap: false,
    minify: 'esbuild',
    lib: {
      entry: resolve(__dirname, 'src/engine/gguf.worker.ts'),
      formats: ['es'],
      fileName: () => 'webml-gguf.worker.js',
    },
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
