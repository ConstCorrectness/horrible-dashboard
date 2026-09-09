/**
 * `openBuffer`'s options, in a leaf file of their own so the buffer-host seam
 * (`host.ts`) can name them without importing `index.tsx` — which opens a
 * WebSocket at module scope and so cannot be pulled into a unit test.
 */

/** Optional knobs when opening a buffer. */
export interface OpenBufferOptions {
  /** Highlighting hint for sources with no extension to infer from (notes). */
  language?: 'javascript' | 'python';
}
