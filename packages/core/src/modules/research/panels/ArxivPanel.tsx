/**
 * ArXiv browser pane (`research.arxiv`) — search, read abstracts, and pull
 * papers into the node: Open PDF downloads into the artifact store + library
 * (tagged `arxiv`) and opens the PDF viewer on it. Singleton: one search
 * surface; papers themselves open as their own viewer panes.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { apiGet, apiPost } from '../../../api';
import { registry } from '../../../registry';
import { toastsStore } from '../../../toasts';
import { getSetting } from '../../../settings';
import type { ArtifactModel } from '../api';
import type { SourceModel } from '../../library/api';

interface ArxivEntry {
  id: string;
  title: string;
  summary: string;
  authors: string[];
  published: string;
  updated: string;
  categories: string[];
  pdf_url: string;
  abs_url: string;
  comment?: string | null;
}

interface SearchResponse {
  query: string;
  total: number;
  start: number;
  entries: ArxivEntry[];
}

interface DownloadResponse {
  artifact: ArtifactModel;
  source: SourceModel;
  entry: ArxivEntry;
}

const CATEGORIES = [
  '',
  'cs.AI',
  'cs.CL',
  'cs.CV',
  'cs.LG',
  'cs.SE',
  'stat.ML',
  'math.OC',
  'quant-ph',
];

const PAGE = 20;

export function ArxivPanel() {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState('relevance');
  const [start, setStart] = useState(0);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [selected, setSelected] = useState<ArxivEntry | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // arxiv id being downloaded
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const runSearch = useCallback(
    (nextStart = 0) => {
      const trimmed = query.trim();
      if (!trimmed && !category) return;
      const seq = ++requestSeq.current;
      setSearching(true);
      setError(null);
      const params = new URLSearchParams({
        query: trimmed,
        start: String(nextStart),
        max_results: String(PAGE),
        sort,
      });
      if (category) params.set('category', category);
      apiGet<SearchResponse>(`/arxiv/search?${params}`)
        .then((res) => {
          if (seq !== requestSeq.current) return; // superseded
          setResult(res);
          setStart(nextStart);
          setSelected(null);
        })
        .catch((err: unknown) => {
          if (seq !== requestSeq.current) return;
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (seq === requestSeq.current) setSearching(false);
        });
    },
    [query, category, sort],
  );

  // Re-run an existing search when the sort/category changes under it. Guarded
  // by an explicit previous-filters comparison so a completing search (result
  // changing) can't retrigger itself.
  const searchRef = useRef(runSearch);
  searchRef.current = runSearch;
  const prevFilters = useRef({ category, sort });
  useEffect(() => {
    const prev = prevFilters.current;
    prevFilters.current = { category, sort };
    const changed = prev.category !== category || prev.sort !== sort;
    if (changed && result) searchRef.current(0);
  }, [category, sort, result]);

  const download = useCallback((entry: ArxivEntry, open: boolean) => {
    setBusy(entry.id);
    apiPost<DownloadResponse>('/arxiv/download', {
      arxiv_id: entry.id,
      library: getSetting<string>('browser.saveLibrary') || 'default',
    })
      .then((res) => {
        toastsStore.add('success', 'Paper saved', res.source.title);
        if (open) {
          registry.openPanel('research.pdfViewer', {
            instanceId: `pdf:${res.artifact.id}`,
            params: { artifactId: res.artifact.id, sourceId: res.source.id },
          });
        }
      })
      .catch((err: unknown) =>
        toastsStore.add(
          'warning',
          'Download failed',
          err instanceof Error ? err.message : String(err),
        ),
      )
      .finally(() => setBusy(null));
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.4rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          alignItems: 'center',
        }}
      >
        <input
          value={query}
          placeholder="Search arXiv — titles, abstracts, authors…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') runSearch(0);
          }}
          style={{ flex: 1 }}
        />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c || 'all categories'}
            </option>
          ))}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="relevance">relevance</option>
          <option value="submittedDate">newest</option>
          <option value="lastUpdatedDate">updated</option>
        </select>
        <button onClick={() => runSearch(0)} disabled={searching}>
          {searching ? '…' : 'Search'}
        </button>
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div
          style={{
            flex: '1 1 45%',
            overflow: 'auto',
            borderRight: '1px solid var(--border)',
          }}
        >
          {error && <div style={{ padding: '0.75rem', color: 'var(--text-dim)' }}>{error}</div>}
          {!result && !error && (
            <div style={{ padding: '0.75rem', color: 'var(--text-dim)', fontSize: '0.8rem' }}>
              Search arXiv, read the abstract, and pull papers you want to keep into the library —
              they land as searchable PDFs.
            </div>
          )}
          {result?.entries.map((entry) => (
            <div
              key={entry.id}
              onClick={() => setSelected(entry)}
              style={{
                padding: '0.5rem 0.6rem',
                cursor: 'pointer',
                borderBottom: '1px solid var(--border)',
                background:
                  selected?.id === entry.id ? 'var(--bg-hover, rgba(128,128,128,0.15))' : undefined,
              }}
            >
              <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>{entry.title}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                {entry.authors.slice(0, 4).join(', ')}
                {entry.authors.length > 4 ? ' et al.' : ''} · {entry.published.slice(0, 10)} ·{' '}
                {entry.categories.slice(0, 3).join(' ')}
              </div>
            </div>
          ))}
          {result && (
            <div
              style={{ display: 'flex', gap: '0.5rem', padding: '0.5rem', alignItems: 'center' }}
            >
              <button
                disabled={start === 0 || searching}
                onClick={() => runSearch(Math.max(0, start - PAGE))}
              >
                ‹ prev
              </button>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                {start + 1}–{Math.min(start + PAGE, result.total)} of {result.total}
              </span>
              <button
                disabled={start + PAGE >= result.total || searching}
                onClick={() => runSearch(start + PAGE)}
              >
                next ›
              </button>
            </div>
          )}
        </div>

        <div style={{ flex: '1 1 55%', overflow: 'auto', padding: '0.75rem' }}>
          {selected ? (
            <>
              <h3 style={{ margin: '0 0 0.25rem' }}>{selected.title}</h3>
              <div
                style={{ fontSize: '0.78rem', color: 'var(--text-dim)', marginBottom: '0.5rem' }}
              >
                {selected.authors.join(', ')}
                <br />
                {selected.id} · {selected.published.slice(0, 10)} · {selected.categories.join(' ')}
                {selected.comment ? ` · ${selected.comment}` : ''}
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
                <button disabled={busy === selected.id} onClick={() => download(selected, true)}>
                  {busy === selected.id ? 'Downloading…' : 'Open PDF'}
                </button>
                <button disabled={busy === selected.id} onClick={() => download(selected, false)}>
                  Save to library
                </button>
                <button
                  onClick={() =>
                    registry.openPanel('browser.view', { params: { url: selected.abs_url } })
                  }
                >
                  abs ↗
                </button>
              </div>
              <p style={{ fontSize: '0.85rem', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                {selected.summary}
              </p>
            </>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
              Select a paper to read its abstract.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
