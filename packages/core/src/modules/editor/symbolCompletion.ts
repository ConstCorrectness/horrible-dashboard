/**
 * Database-backed completion — the editor's "intellisense" as a fast prefix
 * lookup, not a model. One source feeds both surfaces: the dropdown
 * (`dbSymbolSource`, merged into the same popup as the LSP in lsp.ts) and the
 * inline ghost text (`dbGhostFetch`, used by autosuggest in BufferView). Buffers
 * are harvested into the index server-side via `indexBuffer`. The backend is
 * `/editor/complete` + `/editor/symbols/index` (see backend/modules/lsp). Modeled
 * on `frameworkImportSource` in pythonImports.ts. See docs/modules/editor.mdx.
 */
import type {
  Completion,
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from '@codemirror/autocomplete';

import { apiGet, apiPost } from '../../api';

interface DbSymbol {
  symbol: string;
  kind: string;
  detail: string;
  module: string;
}

// Map our stored `kind` to a CodeMirror completion `type` (drives the popup icon).
const KIND_TO_TYPE: Record<string, string> = {
  function: 'function',
  class: 'class',
  variable: 'variable',
  module: 'namespace',
  keyword: 'keyword',
};

/** One indexed prefix query against the symbol DB. Never throws — a failed lookup
 * just yields no completions (the popup stays quiet). */
async function fetchSymbols(lang: string, prefix: string, limit: number): Promise<DbSymbol[]> {
  if (!lang || !prefix) return [];
  const q = `lang=${encodeURIComponent(lang)}&prefix=${encodeURIComponent(prefix)}&limit=${limit}`;
  try {
    const res = await apiGet<{ items: DbSymbol[] }>(`/editor/complete?${q}`);
    return res.items ?? [];
  } catch {
    return [];
  }
}

// Debounced buffer → index push, keyed by source so rapid edits collapse into one.
const indexTimers = new Map<string, number>();

/** Queue a debounced re-index of a buffer's symbols. */
export function indexBuffer(source: string, lang: string, text: string, delayMs = 1200): void {
  const prev = indexTimers.get(source);
  if (prev !== undefined) window.clearTimeout(prev);
  const timer = window.setTimeout(() => {
    indexTimers.delete(source);
    void apiPost('/editor/symbols/index', { source, lang, text }).catch(() => {});
  }, delayMs);
  indexTimers.set(source, timer);
}

/** Index a buffer immediately (e.g. on open) — cancels any pending debounce. */
export function indexBufferNow(source: string, lang: string, text: string): void {
  const prev = indexTimers.get(source);
  if (prev !== undefined) {
    window.clearTimeout(prev);
    indexTimers.delete(source);
  }
  void apiPost('/editor/symbols/index', { source, lang, text }).catch(() => {});
}

/** Dropdown completion source backed by the symbol DB. Bails on member access
 * (`.foo`) so the LSP owns type-aware member completion; ranks below the LSP's
 * in-scope results (`boost: -50`) and above the framework-import source (`-99`). */
export function dbSymbolSource(getLang: () => string | null): CompletionSource {
  return async (context: CompletionContext): Promise<CompletionResult | null> => {
    const word = context.matchBefore(/[A-Za-z_]\w*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    // Member access (`obj.attr`) needs type info the prefix index doesn't have.
    if (context.state.sliceDoc(Math.max(0, word.from - 1), word.from) === '.') return null;
    const lang = getLang();
    const typed = context.state.sliceDoc(word.from, word.to);
    if (!lang || !typed) return null;
    const rows = await fetchSymbols(lang, typed, 25);
    if (!rows.length) return null;
    const options: Completion[] = rows.map((r) => ({
      label: r.symbol,
      type: KIND_TO_TYPE[r.kind] ?? 'text',
      detail: r.detail || undefined,
      boost: -50,
    }));
    return { from: word.from, options, validFor: /^[A-Za-z_]\w*$/ };
  };
}

/** Inline ghost-text producer: the remaining tail of the single best-matching
 * symbol for the token before the cursor, or '' when there's nothing confident to
 * show. Matches the `autosuggest` fetch shape; no model. */
export async function dbGhostFetch(prefix: string, _suffix: string, lang: string): Promise<string> {
  const m = /[A-Za-z_]\w*$/.exec(prefix);
  const token = m ? m[0] : '';
  // Too short → don't guess; a 1-char prefix matches almost anything.
  if (token.length < 2) return '';
  const rows = await fetchSymbols(lang, token, 1);
  const top = rows[0]?.symbol;
  if (!top || !top.startsWith(token) || top.length <= token.length) return '';
  return top.slice(token.length);
}
