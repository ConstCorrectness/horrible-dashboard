/**
 * Knowledge pane (`kb.browse`): the user's knowledge base as a catalog.
 *
 * Three columns. The rail filters (collections, status, kind) and searches; the
 * list shows top-level items — a course is one row with a progress track, its
 * lessons live in its tree; the detail column edits one item and walks its tree.
 * A query switches the list to retrieved passages with their citations, which is
 * the same `kb.search` the agent uses — so what the user sees here is what the
 * agent would cite.
 *
 * Params: `{ itemId?: string; import?: string }` — `import` opens the import sheet
 * with that URL filled in (the browser's "Import this site" lands here).
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

import { usePaneParams } from '../../../panes';
import { STATUSES, type Item, type Kind, type Passage, type Status } from '../api';
import { ImportIcon, KindIcon, SearchIcon } from '../icons';
import { openItem } from '../open';
import {
  getCollections,
  getError,
  getFilters,
  getItems,
  getSearch,
  getSelected,
  getSelectedTree,
  getSummary,
  getTotal,
  importFor,
  initKbWatch,
  isLoading,
  kbVersion,
  refresh,
  runSearch,
  select,
  setFilters,
  subscribeKb,
} from '../store';
import '../kb.css';
import { ImportSheet } from './ImportSheet';
import { ItemDetail } from './ItemDetail';
import { RollingNumber } from './RollingNumber';

const STATUS_SHORT: Record<Status, string> = {
  to_read: 'to read',
  reading: 'reading',
  done: 'done',
  archived: 'archived',
};

const KIND_FACETS: Kind[] = ['course', 'primer', 'lesson', 'article', 'paper', 'bookmark', 'site'];

function delay(i: number): React.CSSProperties {
  // Capped so a 200-row list still finishes arriving in ~300ms.
  return { ['--kb-delay' as string]: `${Math.min(i * 16, 300)}ms` };
}

function host(url?: string | null): string {
  if (!url) return '';
  try {
    return new URL(url).host.replace(/^www\./, '');
  } catch {
    return '';
  }
}

export function KnowledgePane() {
  const params = usePaneParams();
  useSyncExternalStore(subscribeKb, kbVersion);
  const [importing, setImporting] = useState<string | null>(
    typeof params.import === 'string' ? params.import : null,
  );
  const [query, setQuery] = useState('');

  useEffect(() => {
    initKbWatch();
    void refresh();
  }, []);

  useEffect(() => {
    if (typeof params.itemId === 'string') void select(params.itemId);
  }, [params.itemId]);

  useEffect(() => {
    if (typeof params.import === 'string') setImporting(params.import);
  }, [params.import]);

  const filters = getFilters();
  const items = getItems();
  const selected = getSelected();
  const tree = getSelectedTree();
  const summary = getSummary();
  const search = getSearch();
  const error = getError();

  return (
    // The wrapper is the size container: a container query styles the container's
    // descendants, never the container itself, so the grid can't be both.
    <div className="kb-host">
      <div className="kb-pane" data-detail={selected ? 'open' : undefined}>
        <aside className="kb-rail">
          <div className="kb-rail-head">
            <div className="kb-title">Knowledge</div>
            <div className="kb-mono" style={{ marginTop: 'var(--space-2)' }}>
              <RollingNumber value={summary?.items ?? 0} /> items ·{' '}
              <RollingNumber value={summary?.indexed ?? 0} /> indexed
            </div>
            <form
              style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}
              onSubmit={(e) => {
                e.preventDefault();
                void runSearch(query);
              }}
            >
              <input
                type="text"
                value={query}
                placeholder="Ask your material…"
                onChange={(e) => {
                  setQuery(e.target.value);
                  if (!e.target.value.trim()) void runSearch('');
                }}
              />
              <button type="submit" title="Search passages" disabled={search.searching}>
                <SearchIcon size={13} />
              </button>
            </form>
          </div>

          <div className="kb-rail-scroll">
            <div className="kb-label">Collections</div>
            <button
              type="button"
              className="kb-facet"
              aria-pressed={!filters.collection}
              onClick={() => void setFilters({ ...filters, collection: undefined })}
            >
              All
            </button>
            {getCollections().map((c) => (
              <button
                key={c.id}
                type="button"
                className="kb-facet"
                aria-pressed={filters.collection === c.name}
                onClick={() =>
                  void setFilters({
                    ...filters,
                    collection: filters.collection === c.name ? undefined : c.name,
                  })
                }
              >
                {c.name}
                <span className="kb-mono">{c.item_count}</span>
              </button>
            ))}

            <div className="kb-label">Status</div>
            <div className="kb-chips">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="kb-chip"
                  aria-pressed={filters.status === s}
                  onClick={() =>
                    void setFilters({ ...filters, status: filters.status === s ? undefined : s })
                  }
                >
                  {STATUS_SHORT[s]}
                </button>
              ))}
            </div>

            <div className="kb-label">Kind</div>
            <div className="kb-chips">
              {KIND_FACETS.filter((k) => (summary?.by_kind[k] ?? 0) > 0 || filters.kind === k).map(
                (k) => (
                  <button
                    key={k}
                    type="button"
                    className="kb-chip"
                    aria-pressed={filters.kind === k}
                    onClick={() =>
                      void setFilters({ ...filters, kind: filters.kind === k ? undefined : k })
                    }
                  >
                    {k} {summary?.by_kind[k] ?? 0}
                  </button>
                ),
              )}
            </div>
          </div>

          <div className="kb-rail-foot">
            <button
              type="button"
              style={{ flex: 1, justifyContent: 'center' }}
              onClick={() => setImporting('')}
            >
              <ImportIcon size={13} />
              Import site
            </button>
          </div>
        </aside>

        <section className="kb-list" aria-label="Items">
          <div className="kb-list-head">
            <span className="kb-title" style={{ fontSize: 'var(--fs-label)' }}>
              {search.passages ? 'Passages' : (filters.collection ?? 'Everything')}
            </span>
            <span className="kb-mono">
              {search.passages
                ? `${search.passages.length} for “${search.query}”`
                : `${getTotal()} items`}
            </span>
            {isLoading() && <span className="kb-mono">· loading</span>}
          </div>
          {error && <div className="kb-error">{error}</div>}
          <div className="kb-list-scroll">
            {search.passages ? (
              <Passages passages={search.passages} />
            ) : items.length === 0 && !isLoading() ? (
              <Empty
                onImport={() => setImporting('')}
                filtered={Boolean(filters.collection || filters.status || filters.kind)}
              />
            ) : (
              items.map((item, i) => (
                <Row key={item.id} item={item} index={i} selected={item.id === selected?.id} />
              ))
            )}
          </div>
        </section>

        {selected ? (
          <ItemDetail item={selected} tree={tree} progress={importFor(selected)} />
        ) : (
          <div
            className="kb-detail kb-detail-idle"
            style={{ display: 'grid', placeItems: 'center' }}
          >
            <div className="kb-mono" style={{ textAlign: 'center', padding: 'var(--space-6)' }}>
              select an item — or save pages from the browser with the star
            </div>
          </div>
        )}

        {importing !== null && (
          <ImportSheet
            initialUrl={importing}
            onClose={() => setImporting(null)}
            onStarted={(rootId) => {
              setImporting(null);
              void refresh().then(() => select(rootId));
            }}
          />
        )}
      </div>
    </div>
  );
}

function Row({ item, index, selected }: { item: Item; index: number; selected: boolean }) {
  const progress = item.progress && item.progress.total > 0 ? item.progress : null;
  return (
    <button
      type="button"
      className="kb-row"
      aria-selected={selected}
      style={delay(index)}
      onClick={() => void select(item.id)}
      onDoubleClick={() => openItem(item, item.docpage_id || item.artifact_id ? 'offline' : 'live')}
      title={item.url ?? item.title}
    >
      <span className="kb-row-kind">
        <KindIcon kind={item.kind} size={15} />
      </span>
      <span style={{ minWidth: 0 }}>
        <span className="kb-row-title" style={{ display: 'block' }}>
          {item.title}
        </span>
        <span className="kb-mono kb-row-meta" style={{ display: 'block' }}>
          {[
            host(item.url),
            progress
              ? `${progress.done}/${progress.total} lessons`
              : item.child_count
                ? `${item.child_count} pages`
                : null,
            item.collections.join(' · ') || null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <span
          className="kb-dot"
          data-state={item.index_status}
          title={`index: ${item.index_status}`}
        />
        <span className="kb-status" data-status={item.status}>
          {STATUS_SHORT[item.status]}
        </span>
      </span>
      {progress && (
        <span className="kb-progress" aria-hidden="true">
          <span style={{ transform: `scaleX(${progress.done / progress.total})` }} />
        </span>
      )}
    </button>
  );
}

function Passages({ passages }: { passages: Passage[] }) {
  if (passages.length === 0) {
    return (
      <div className="kb-empty kb-mono">
        nothing in your knowledge base matched — empty means not in your material, not that the
        answer doesn't exist
      </div>
    );
  }
  return (
    <>
      {passages.map((p, i) => (
        <button
          key={`${p.source_id}-${i}`}
          type="button"
          className="kb-passage"
          style={delay(i)}
          onClick={() => p.item_id && void select(p.item_id)}
          onDoubleClick={() => {
            const item = getItems().find((it) => it.id === p.item_id) ?? getSelected();
            if (item && item.id === p.item_id) openItem(item, item.docpage_id ? 'offline' : 'live');
          }}
          title={p.link ?? p.url ?? ''}
        >
          <div className="kb-passage-cite">
            [{i + 1}] {p.title}
            {p.section ? <span className="kb-mono"> › {p.section}</span> : null}
          </div>
          <div className="kb-passage-text">{p.text.replace(/^.*\n\n/, '')}</div>
          <div className="kb-mono" style={{ marginTop: 'var(--space-2)' }}>
            {host(p.url)} · score {p.score.toFixed(3)}
            {p.docpage_id || p.artifact_id ? ' · offline' : ''}
          </div>
        </button>
      ))}
    </>
  );
}

function Empty({ onImport, filtered }: { onImport: () => void; filtered: boolean }) {
  if (filtered) return <div className="kb-empty kb-mono">nothing matches these filters</div>;
  return (
    <div className="kb-empty">
      <div className="kb-title" style={{ marginBottom: 'var(--space-3)' }}>
        Nothing saved yet
      </div>
      <div className="kb-mono" style={{ lineHeight: 1.6, marginBottom: 'var(--space-5)' }}>
        Star a page in the browser to save it, or import a whole site — ML primers, a course — to
        read offline and ask the agent about.
      </div>
      <button type="button" onClick={onImport}>
        <ImportIcon size={13} />
        Import a site
      </button>
    </div>
  );
}
