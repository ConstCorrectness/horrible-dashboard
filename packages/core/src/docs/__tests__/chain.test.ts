// @vitest-environment happy-dom
/**
 * The chain's contract: walk the configured sources in order, stop at the first
 * answer, and never throw at a hover handler.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_DOC_SOURCES,
  lookupDocs,
  parseDocSources,
  setLspDocResolver,
  type DocEntry,
} from '../chain';
import { symbolAt } from '../cm-docs';

const entry = (source: DocEntry['source']): DocEntry => ({
  source,
  title: source,
  signature: '',
  body: `from ${source}`,
});

/** Stub `/api/docs/lookup`, answering only for the sources named in `answers`. */
function stubBackend(answers: Partial<Record<string, boolean>>, calls: string[][] = []) {
  return vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { sources: string[] };
    calls.push(body.sources);
    const tried: string[] = [];
    for (const source of body.sources) {
      tried.push(source);
      // The real backend stops at the first source that answers; so does this.
      if (answers[source]) {
        return {
          ok: true,
          json: async () => ({ entries: [entry(source as DocEntry['source'])], tried, notes: [] }),
        } as Response;
      }
    }
    return { ok: true, json: async () => ({ entries: [], tried, notes: [] }) } as Response;
  });
}

beforeEach(() => {
  setLspDocResolver(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setLspDocResolver(null);
});

describe('parseDocSources', () => {
  it('parses the default into the documented order', () => {
    expect(parseDocSources(DEFAULT_DOC_SOURCES)).toEqual(['kernel', 'lsp', 'index', 'web']);
  });

  it('preserves a user’s order and drops duplicates', () => {
    expect(parseDocSources('web, index, web')).toEqual(['web', 'index']);
  });

  it('drops an unknown name instead of failing the whole setting', () => {
    // Hand-edited string: one typo should cost that source, not all documentation.
    expect(parseDocSources('kernel,nonsense,index')).toEqual(['kernel', 'index']);
  });

  it('treats an empty or missing value as no sources', () => {
    expect(parseDocSources('')).toEqual([]);
    expect(parseDocSources(undefined)).toEqual([]);
  });
});

describe('lookupDocs', () => {
  it('stops at the first source that answers', async () => {
    const calls: string[][] = [];
    vi.stubGlobal('fetch', stubBackend({ index: true, web: true }, calls));
    const res = await lookupDocs({ symbol: 'json.dumps', sources: ['index', 'web'] });
    expect(res.entries.map((e) => e.source)).toEqual(['index']);
    // Batched into one request; the backend does the stopping.
    expect(calls).toEqual([['index', 'web']]);
  });

  it('falls through a source with nothing to the next', async () => {
    vi.stubGlobal('fetch', stubBackend({ web: true }));
    const res = await lookupDocs({ symbol: 'x', sources: ['index', 'web'] });
    expect(res.entries.map((e) => e.source)).toEqual(['web']);
  });

  it('resolves lsp in the frontend and batches the backend run around it', async () => {
    const calls: string[][] = [];
    vi.stubGlobal('fetch', stubBackend({ index: true }, calls));
    setLspDocResolver(async () => []);
    const res = await lookupDocs({
      symbol: 'x',
      sources: ['kernel', 'lsp', 'index', 'web'],
    });
    expect(res.entries.map((e) => e.source)).toEqual(['index']);
    // `kernel` alone, then `index,web` — the lsp step splits the backend run.
    expect(calls).toEqual([['kernel'], ['index', 'web']]);
    expect(res.tried).toContain('lsp');
  });

  it('lets the lsp resolver win when it answers', async () => {
    const fetchSpy = stubBackend({ index: true });
    vi.stubGlobal('fetch', fetchSpy);
    setLspDocResolver(async () => [entry('lsp')]);
    const res = await lookupDocs({ symbol: 'x', sources: ['lsp', 'index'] });
    expect(res.entries.map((e) => e.source)).toEqual(['lsp']);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('notes an absent lsp resolver and carries on', async () => {
    vi.stubGlobal('fetch', stubBackend({ index: true }));
    const res = await lookupDocs({ symbol: 'x', sources: ['lsp', 'index'] });
    expect(res.entries.map((e) => e.source)).toEqual(['index']);
    expect(res.notes.join(' ')).toContain('lsp');
  });

  it('never throws when the network fails', async () => {
    // A hover handler has nowhere to put an exception.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    const res = await lookupDocs({ symbol: 'x', sources: ['index'] });
    expect(res.entries).toEqual([]);
    expect(res.tried).toEqual(['index']);
  });

  it('reports a non-ok response as a note, not an entry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500 }) as Response),
    );
    const res = await lookupDocs({ symbol: 'x', sources: ['index'] });
    expect(res.entries).toEqual([]);
    expect(res.notes.join(' ')).toContain('500');
  });

  it('does nothing without a symbol or code', async () => {
    const fetchSpy = stubBackend({ index: true });
    vi.stubGlobal('fetch', fetchSpy);
    const res = await lookupDocs({ symbol: '', sources: ['index'] });
    expect(res.tried).toEqual([]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe('symbolAt', () => {
  it('takes the dotted path so the index can disambiguate the module', () => {
    const code = 'x = pd.DataFrame(y)';
    expect(symbolAt(code, 8).text).toBe('pd.DataFrame');
  });

  it('trims a trailing dot so hovering after `df.` asks about `df`', () => {
    expect(symbolAt('df.', 3).text).toBe('df');
  });

  it('trims a leading dot rather than asking about "."', () => {
    // `.merge` on a continuation line: the name is `merge`, not `.merge`.
    expect(symbolAt('  .merge(other)', 4).text).toBe('merge');
  });

  it('returns empty when nothing adjoins the position', () => {
    // Between the space and the `=` — no identifier on either side.
    expect(symbolAt('a = 1', 2).text).toBe('');
    // But a position hard against a name still resolves it: CodeMirror hover
    // offsets sit *between* characters, so requiring a strict interior hit would
    // make the last character of every symbol dead.
    expect(symbolAt('a = 1', 1).text).toBe('a');
  });

  it('reports the range it matched, for tooltip anchoring', () => {
    const { from, to, text } = symbolAt('foo = bar', 7);
    expect([from, to]).toEqual([6, 9]);
    expect(text).toBe('bar');
  });
});
