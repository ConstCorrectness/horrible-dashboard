/**
 * The Library panel: add blog URLs and notes to a library, watch them ingest live,
 * and semantic-search the collection. A thin consumer of the shared store
 * (`store.ts`); all state and the `library` `/ws` subscription live there.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { useAgentContext } from '../../agent-context';
import { ApiError } from '../../api';
import { dialogs } from '../../dialogs';
import { useSetting } from '../../settings';
import { toastsStore } from '../../toasts';
import type { SourceModel, SourceStatus, SourceType } from './api';
import {
  addSource,
  clearSearch,
  getCurrentLibrary,
  getError,
  getLibraries,
  getSearch,
  getSources,
  initLibraryWatch,
  libraryVersion,
  removeSource,
  runSearch,
  setCurrentLibrary,
  subscribeLibrary,
} from './store';

const SOURCE_ICON: Record<SourceType, string> = {
  blog: '🌐',
  note: '📝',
  image: '🖼',
  video: '🎬',
};

const STATUS_LABEL: Record<SourceStatus, string> = {
  queued: 'Queued',
  fetching: 'Fetching',
  chunking: 'Chunking',
  embedding: 'Embedding',
  ready: 'Ready',
  failed: 'Failed',
};

function hostOf(url?: string | null): string {
  if (!url) return '';
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function parseTags(raw: string): string[] {
  return raw
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

export function LibraryPanel() {
  useSyncExternalStore(subscribeLibrary, libraryVersion);
  const defaultLibrary = useSetting<string>('library.defaultLibrary') ?? 'default';

  useEffect(() => {
    initLibraryWatch();
    void setCurrentLibrary(defaultLibrary);
  }, [defaultLibrary]);

  const library = getCurrentLibrary();
  const sources = getSources();
  const libraries = getLibraries();
  const search = getSearch();
  const error = getError();

  useAgentContext(() => ({
    library,
    sources: sources.map((s) => ({
      id: s.id,
      title: s.title,
      type: s.type,
      status: s.status,
    })),
  }));

  const [formOpen, setFormOpen] = useState(false);
  const [mode, setMode] = useState<'blog' | 'note'>('blog');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [tags, setTags] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [query, setQuery] = useState('');

  const libNames = libraries.map((l) => l.name);
  if (!libNames.includes(library)) libNames.unshift(library);

  async function submit(): Promise<void> {
    setSubmitting(true);
    try {
      if (mode === 'blog') {
        if (!url.trim()) {
          toastsStore.add('warning', 'URL required', 'Paste a blog or article URL.');
          return;
        }
        await addSource({ type: 'blog', url: url.trim(), tags: parseTags(tags) });
      } else {
        if (!text.trim()) {
          toastsStore.add('warning', 'Text required', 'Write or paste some notes.');
          return;
        }
        await addSource({
          type: 'note',
          title: title.trim() || undefined,
          text: text.trim(),
          tags: parseTags(tags),
        });
      }
      setUrl('');
      setTitle('');
      setText('');
      setTags('');
      setFormOpen(false);
    } catch (e) {
      toastsStore.add('error', 'Ingest failed', e instanceof ApiError ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function newLibrary(): Promise<void> {
    const name = await dialogs.prompt({
      title: 'New library',
      defaultValue: '',
      confirmLabel: 'Create',
    });
    if (name && name.trim()) await setCurrentLibrary(name.trim());
  }

  async function del(source: SourceModel): Promise<void> {
    const ok = await dialogs.confirm({
      title: 'Remove source',
      message: `Remove "${source.title}" and its ${source.chunk_count} chunks? This can't be undone.`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      await removeSource(source.id);
    } catch (e) {
      toastsStore.add('error', 'Delete failed', e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="lib-panel">
      <div className="lib-toolbar">
        <select
          className="lib-select"
          value={library}
          onChange={(e) => void setCurrentLibrary(e.target.value)}
        >
          {libNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button className="lib-btn" onClick={() => void newLibrary()}>
          + Library
        </button>
        <div className="lib-searchbox">
          <input
            className="lib-input"
            placeholder="Search this library…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runSearch(query);
            }}
          />
          <button
            className="lib-btn"
            onClick={() => void runSearch(query)}
            disabled={search.searching}
          >
            Search
          </button>
          {search.results !== null && (
            <button className="lib-btn" onClick={clearSearch}>
              Clear
            </button>
          )}
        </div>
        <button className="lib-btn lib-btn-primary" onClick={() => setFormOpen((o) => !o)}>
          {formOpen ? 'Cancel' : '+ Add source'}
        </button>
      </div>

      {formOpen && (
        <div className="lib-form">
          <div className="lib-tabs">
            <button
              className={mode === 'blog' ? 'lib-tab active' : 'lib-tab'}
              onClick={() => setMode('blog')}
            >
              From URL
            </button>
            <button
              className={mode === 'note' ? 'lib-tab active' : 'lib-tab'}
              onClick={() => setMode('note')}
            >
              Paste notes
            </button>
          </div>
          {mode === 'blog' ? (
            <input
              className="lib-input"
              placeholder="https://example.com/post"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          ) : (
            <>
              <input
                className="lib-input"
                placeholder="Title (optional)"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <textarea
                className="lib-textarea"
                placeholder="Paste or write notes…"
                rows={6}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </>
          )}
          <input
            className="lib-input"
            placeholder="tags, comma, separated"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
          <button
            className="lib-btn lib-btn-primary"
            onClick={() => void submit()}
            disabled={submitting}
          >
            {submitting ? 'Adding…' : 'Add to library'}
          </button>
        </div>
      )}

      {error && <div className="lib-error">{error}</div>}

      {search.results !== null ? (
        <div className="lib-results">
          <div className="lib-results-head">
            {search.searching
              ? `Searching “${search.query}”…`
              : `${search.results.length} result${
                  search.results.length === 1 ? '' : 's'
                } for “${search.query}”`}
          </div>
          {!search.searching && search.results.length === 0 && (
            <div className="lib-empty">No matches in this library.</div>
          )}
          {search.results.map((g) => (
            <div className="lib-result" key={g.source_id}>
              <div className="lib-result-title">
                {g.url ? (
                  <a href={g.url} target="_blank" rel="noreferrer">
                    {g.title}
                  </a>
                ) : (
                  g.title
                )}
                {/* A visual match is worth saying out loud: it means the *picture*
                    matched, which is why there may be no snippet below it. */}
                {g.matched_by?.includes('clip') && (
                  <span className="lib-badge" title="The image itself matched this query">
                    👁 visual
                  </span>
                )}
                <span className="lib-score">{Math.round(g.top_score * 100)}%</span>
              </div>
              {g.asset && (
                <a href={g.asset.page_url ?? g.asset.src} target="_blank" rel="noreferrer">
                  <img
                    className="lib-thumb"
                    src={g.asset.kind === 'image' ? g.asset.src : (g.asset.poster ?? g.asset.src)}
                    alt={g.asset.alt ?? g.title}
                    loading="lazy"
                  />
                </a>
              )}
              {g.chunks.slice(0, 2).map((c) => (
                <p className="lib-snippet" key={c.chunk_index}>
                  {c.text.slice(0, 280)}
                  {c.text.length > 280 ? '…' : ''}
                </p>
              ))}
            </div>
          ))}
        </div>
      ) : sources.length === 0 ? (
        <div className="lib-empty">
          No sources yet — add a blog URL or paste notes to build up “{library}”.
        </div>
      ) : (
        <table className="lib-table">
          <thead>
            <tr>
              <th />
              <th>Title</th>
              <th>Tags</th>
              <th>Chunks</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id}>
                <td className="lib-icon">{SOURCE_ICON[s.type] ?? '📝'}</td>
                <td>
                  <div className="lib-title">
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noreferrer">
                        {s.title}
                      </a>
                    ) : (
                      s.title
                    )}
                  </div>
                  <div className="lib-sub">
                    {s.author && <span>{s.author}</span>}
                    {hostOf(s.url) && <span>{hostOf(s.url)}</span>}
                  </div>
                </td>
                <td>
                  {s.tags.map((t) => (
                    <span className="lib-tag" key={t}>
                      {t}
                    </span>
                  ))}
                </td>
                <td className="lib-num">{s.chunk_count}</td>
                <td>
                  <span className={`lib-status lib-status-${s.status}`}>
                    {STATUS_LABEL[s.status]}
                  </span>
                  {s.status === 'failed' && s.error && (
                    <span className="lib-errmark" title={s.error}>
                      {' '}
                      ⓘ
                    </span>
                  )}
                </td>
                <td>
                  <button
                    className="lib-btn lib-btn-ghost"
                    title="Remove"
                    onClick={() => void del(s)}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
