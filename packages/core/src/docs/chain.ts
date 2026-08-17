/**
 * The documentation chain: turn "what is this symbol" into something to show.
 *
 * Four sources, ordered by how much they know about *this* code rather than about
 * code in general:
 *
 * | source   | knows                                        | costs        |
 * |----------|----------------------------------------------|--------------|
 * | `kernel` | the live notebook namespace (`df` is a thing) | a kernel RTT |
 * | `lsp`    | the open project, statically                  | a pipe RTT   |
 * | `index`  | installed packages + stdlib, offline          | a query      |
 * | `web`    | prose no docstring contains                   | the network  |
 *
 * The chain **stops at the first source that answers**. Three renderings of the
 * same function is not three times the information, and the order is already the
 * statement of which to believe when they disagree.
 *
 * `lsp` is resolved in the frontend because the LSP client lives here (the backend
 * is a dumb JSON-RPC pipe — docs/architecture/lsp.mdx); the other three are one
 * POST. Consecutive backend sources are batched into a single request, so the
 * common case is one round trip and the worst case is two.
 */
import { apiUrl } from '../origin';
import { getSetting } from '../settings';

export type DocSourceId = 'kernel' | 'lsp' | 'index' | 'web';

export const DOC_SOURCE_IDS: readonly DocSourceId[] = ['kernel', 'lsp', 'index', 'web'];

/** The default order, and the value of the `docs.sources` setting out of the box. */
export const DEFAULT_DOC_SOURCES = 'kernel,lsp,index,web';

export interface DocEntry {
  source: DocSourceId;
  title: string;
  signature: string;
  /** Markdown. Render it through a sanitizer — a docstring is untrusted text. */
  body: string;
  url?: string;
}

export interface DocLookupRequest {
  /** The symbol under the cursor. May be empty when `code`+`cursorPos` are given. */
  symbol: string;
  lang?: string;
  /** Notebook path, for the `kernel` source. */
  notebookPath?: string;
  /** Source text + offset, so `kernel` can resolve an expression, not just a name. */
  code?: string;
  cursorPos?: number;
  /** Overrides the configured order. Used by callers with no LSP (notebook cells). */
  sources?: DocSourceId[];
}

export interface DocLookupResult {
  entries: DocEntry[];
  /** Every source consulted — lets a caller say "nothing found" vs "none enabled". */
  tried: DocSourceId[];
  notes: string[];
}

/** Resolver for the frontend-only `lsp` source, installed by the editor module. */
export type LspDocResolver = (req: DocLookupRequest) => Promise<DocEntry[]>;

let lspResolver: LspDocResolver | null = null;

/**
 * Register the LSP source. The editor module installs this; nothing else may.
 *
 * A registration seam rather than a direct import because the chain lives in core
 * alongside the settings it reads, while the LSP client is an editor-module
 * concern — and a notebook cell, which has no LSP at all, must be able to use the
 * chain without dragging the editor in.
 */
export function setLspDocResolver(resolver: LspDocResolver | null): void {
  lspResolver = resolver;
}

/** Parse `docs.sources` into a validated, deduped order. */
export function parseDocSources(raw: string | undefined): DocSourceId[] {
  const seen = new Set<DocSourceId>();
  for (const part of (raw ?? '').split(',')) {
    const id = part.trim().toLowerCase() as DocSourceId;
    // Unknown names are dropped rather than failing the whole setting: this is a
    // hand-edited string, and one typo should cost that one source, not all docs.
    if (DOC_SOURCE_IDS.includes(id)) seen.add(id);
  }
  return [...seen];
}

/** The sources this install has enabled, in order. */
export function enabledDocSources(): DocSourceId[] {
  return parseDocSources(getSetting<string>('docs.sources') ?? DEFAULT_DOC_SOURCES);
}

/** Group an ordered source list into runs of backend / frontend sources. */
function runs(sources: DocSourceId[]): { backend: boolean; ids: DocSourceId[] }[] {
  const out: { backend: boolean; ids: DocSourceId[] }[] = [];
  for (const id of sources) {
    const backend = id !== 'lsp';
    const last = out[out.length - 1];
    if (last && last.backend === backend) last.ids.push(id);
    else out.push({ backend, ids: [id] });
  }
  return out;
}

async function lookupBackend(req: DocLookupRequest, ids: DocSourceId[]): Promise<DocLookupResult> {
  const res = await fetch(apiUrl('/api/docs/lookup'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol: req.symbol,
      sources: ids,
      lang: req.lang ?? 'python',
      context: {
        notebook_path: req.notebookPath ?? null,
        code: req.code ?? null,
        cursor_pos: req.cursorPos ?? null,
      },
    }),
  });
  if (!res.ok) return { entries: [], tried: ids, notes: [`lookup failed (${res.status})`] };
  const body = (await res.json()) as DocLookupResult;
  return { entries: body.entries ?? [], tried: body.tried ?? ids, notes: body.notes ?? [] };
}

/**
 * Walk the chain and return the first source's answer.
 *
 * Never throws: a documentation popup that raises is strictly worse than one that
 * says nothing, and the caller is usually a hover handler with nowhere to put an
 * error.
 */
export async function lookupDocs(req: DocLookupRequest): Promise<DocLookupResult> {
  const sources = req.sources ?? enabledDocSources();
  const tried: DocSourceId[] = [];
  const notes: string[] = [];
  if (!req.symbol && req.code === undefined) return { entries: [], tried, notes };

  for (const run of runs(sources)) {
    if (!run.backend) {
      tried.push('lsp');
      if (!lspResolver) {
        notes.push('lsp source not available here');
        continue;
      }
      try {
        const entries = await lspResolver(req);
        if (entries.length) return { entries, tried, notes };
      } catch {
        notes.push('lsp failed');
      }
      continue;
    }
    try {
      const result = await lookupBackend(req, run.ids);
      tried.push(...result.tried);
      notes.push(...result.notes);
      if (result.entries.length) return { entries: result.entries, tried, notes };
    } catch {
      tried.push(...run.ids);
      notes.push('lookup failed');
    }
  }
  return { entries: [], tried, notes };
}
