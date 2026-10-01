/**
 * Discover (`discover.browse`): one browser over every external catalog the app
 * works with, a section per catalog.
 *
 * Each section opens on a real, labelled default feed — trending on the Hub, newest
 * in cs.LG, most-starred ML repos pushed this week — instead of an empty search box,
 * and the label line says exactly what the list is and how old it is. Upstream
 * trouble is shown as what it is: "connect Kaggle", "rate limit resets in 41s",
 * "showing cached results", never a bare error.
 *
 * All state lives in `../store`, so switching sections (which unmounts this body)
 * keeps every query, page and selection.
 */
import './discover.css';

import { useEffect, useState } from 'react';

import { DataList, DataRow } from '../../../DataList';
import { IconRetry, IconSearch } from '../../../glyphs';
import { usePaneSection } from '../../../layout/use-sections';
import { registry } from '../../../registry';
import type { DiscoverItem, DiscoverPage, KindSpec, SourceSpec } from '../api';
import { ago, rowMetrics, sectionConfig } from '../format';
import {
  detailKey,
  effectiveFilter,
  effectiveSort,
  ensureLoaded,
  ensureSpecs,
  kindSpecOf,
  load,
  loadMore,
  refresh,
  select,
  setActiveKind,
  setDraft,
  setFilter,
  setSort,
  submit,
  useDiscover,
  viewOf,
} from '../store';
import { DetailView } from './DetailView';
import { MetricCell } from './MetricCell';

export function DiscoverPane() {
  const { section } = usePaneSection();
  const config = sectionConfig(section);
  const state = useDiscover();
  const source = config.source;
  const kind = state.kinds[config.id] ?? config.kinds[0];
  const spec: SourceSpec | undefined = state.specs?.find((s) => s.id === source);
  const kindSpec = kindSpecOf(state, source, kind);
  const view = viewOf(state, source, kind);

  useEffect(() => {
    void ensureSpecs();
  }, []);

  // Wait for specs before the first load, so the first request carries the spec's
  // default sort and the label line agrees with the select showing it.
  useEffect(() => {
    if (state.specs) ensureLoaded(source, kind);
  }, [state.specs, source, kind]);

  const selectedItem = view.items.find((i) => i.id === view.selected) ?? null;
  const detail = view.selected ? state.details[detailKey(source, kind, view.selected)] : undefined;

  return (
    <div className="dc-pane">
      <header className="dc-head">
        <div className="dc-titlebar">
          <h2 className="dc-h">{config.label}</h2>
          <span className="dc-meta">
            {spec?.label ?? source}
            {spec && spec.requires_auth && !spec.connected ? ' · not connected' : ''}
            {spec && !spec.requires_auth && spec.connected ? ' · connected' : ''}
          </span>
          <span className="dc-spacer" />
          <button
            type="button"
            className="dc-icon-btn"
            onClick={() => refresh(source, kind)}
            disabled={view.loading}
            title="Refresh — bypass the cache"
            aria-label="Refresh"
          >
            <IconRetry className={`hd-icon${view.loading ? ' dc-spin' : ''}`} />
          </button>
        </div>

        {config.kinds.length > 1 && (
          <div className="dc-kinds" role="tablist" aria-label={`${config.label} kinds`}>
            {config.kinds.map((k) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={k === kind}
                className="dc-kind"
                onClick={() => setActiveKind(config.id, k)}
              >
                {kindSpecOf(state, source, k)?.label ?? k}
              </button>
            ))}
          </div>
        )}

        {kindSpec && <Controls source={source} kind={kind} spec={kindSpec} />}

        {spec?.auth_hint && !spec.requires_auth && <p className="dc-hint">{spec.auth_hint}</p>}

        <FeedLine page={view.page} loading={view.loading} />
      </header>

      <div className="dc-body">
        <section className="dc-results" aria-label="Results" aria-busy={view.loading}>
          {state.specsError && !state.specs && (
            <Banner tone="fail" title="Couldn’t reach the backend">
              {state.specsError}
              <button type="button" className="dc-text-btn" onClick={() => void ensureSpecs(true)}>
                Try again
              </button>
            </Banner>
          )}
          {view.error && (
            <Banner tone="fail" title="Request failed">
              {view.error}
              <button type="button" className="dc-text-btn" onClick={() => void load(source, kind)}>
                Try again
              </button>
            </Banner>
          )}
          {view.page && (
            // Keyed by arrival, so a rate-limit countdown restarts with each answer.
            <StatusBanner
              key={`${view.page.fetched_at}:${view.page.status}`}
              page={view.page}
              source={source}
              kind={kind}
            />
          )}

          {view.loading && view.items.length === 0 && <Skeleton />}

          {!view.loading && view.page && view.items.length === 0 && view.page.status === 'ok' && (
            <p className="dc-empty">
              {view.q ? `Nothing matches “${view.q}”.` : 'This feed is empty right now.'}
            </p>
          )}

          {view.items.length > 0 && (
            <DataList label={view.page?.feed_label ?? config.label}>
              {view.items.map((item, i) => (
                <ResultRow
                  key={item.id}
                  item={item}
                  index={i}
                  selected={item.id === view.selected}
                  onSelect={() => void select(source, kind, item.id)}
                />
              ))}
            </DataList>
          )}

          {view.page?.cursor_next && (
            <button
              type="button"
              className="dc-more"
              onClick={() => loadMore(source, kind)}
              disabled={view.loadingMore}
            >
              {view.loadingMore ? 'Loading…' : 'Load more'}
            </button>
          )}
        </section>

        <section className="dc-detail" aria-label="Details">
          {selectedItem ? (
            <DetailView source={source} kind={kind} item={selectedItem} detail={detail} />
          ) : (
            <div className="dc-detail-empty">
              <p className="dc-h dc-h-sm">Nothing selected</p>
              <p className="dc-meta">Pick a row to read its card, facts and links.</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function Controls({ source, kind, spec }: { source: string; kind: string; spec: KindSpec }) {
  const state = useDiscover();
  const view = viewOf(state, source, kind);
  return (
    <div className="dc-controls">
      {spec.searchable && (
        <form
          className="dc-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            submit(source, kind);
          }}
        >
          <IconSearch className="hd-icon dc-search-icon" aria-hidden="true" />
          <input
            type="search"
            value={view.draft}
            placeholder={spec.search_placeholder}
            aria-label={spec.search_placeholder}
            onChange={(e) => setDraft(source, kind, e.target.value)}
          />
        </form>
      )}
      {spec.sorts.length > 0 && (
        <label className="dc-field">
          <span className="dc-label">Sort</span>
          <select
            value={effectiveSort(state, source, kind)}
            onChange={(e) => setSort(source, kind, e.target.value)}
          >
            {spec.sorts.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {spec.filters.map((f) => (
        <label key={f.id} className="dc-field">
          <span className="dc-label">{f.label}</span>
          <select
            value={effectiveFilter(state, source, kind, f.id)}
            onChange={(e) => setFilter(source, kind, f.id, e.target.value)}
          >
            {f.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );
}

/** What this list is, how old it is, how much of it there is. */
function FeedLine({ page, loading }: { page: DiscoverPage | null; loading: boolean }) {
  // Re-render once a minute so "3m ago" doesn't freeze.
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 60_000);
    return () => clearInterval(t);
  }, []);
  if (!page) return <p className="dc-feed">{loading ? 'Loading…' : ' '}</p>;
  const parts = [page.feed_label];
  if (page.total !== null) parts.push(`${page.total.toLocaleString()} total`);
  if (page.fetched_at) parts.push(`fetched ${ago(page.fetched_at)}`);
  return (
    <p className="dc-feed" title={page.feed_label}>
      {parts.join(' · ')}
      {page.stale && <span className="dc-stale"> · cached</span>}
    </p>
  );
}

function ResultRow({
  item,
  index,
  selected,
  onSelect,
}: {
  item: DiscoverItem;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const lead = item.badges[0];
  const metrics = rowMetrics(item.metrics);
  return (
    // Metrics go on their own line under the title, not in the head's meta slot:
    // repo ids are long, and three labelled figures beside them squeezed every
    // title down to "EDGEO/AU…" — the identity is the thing being scanned.
    <DataRow
      title={<span title={item.title}>{item.title}</span>}
      hideMark
      index={index}
      selected={selected}
      onClick={onSelect}
      badge={lead ? <span data-tone={lead.tone}>{lead.label}</span> : undefined}
    >
      {item.subtitle && <span className="dc-row-sub">{item.subtitle}</span>}
      {metrics.length > 0 && (
        <span className="dc-row-metrics">
          {metrics.map((m) => (
            <MetricCell key={m.key} metric={m} />
          ))}
        </span>
      )}
      {item.description && <span className="dc-row-desc">{item.description}</span>}
    </DataRow>
  );
}

function Banner({
  tone,
  title,
  children,
}: {
  tone: 'fail' | 'warn' | 'info';
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="dc-banner" data-tone={tone} role={tone === 'fail' ? 'alert' : 'status'}>
      <span className="dc-banner-title">{title}</span>
      <div className="dc-banner-body">{children}</div>
    </div>
  );
}

/** The upstream's status, said plainly. */
function StatusBanner({
  page,
  source,
  kind,
}: {
  page: DiscoverPage;
  source: string;
  kind: string;
}) {
  const remaining = useCountdown(page);
  const cached = page.stale ? ` Showing results from ${ago(page.fetched_at)}.` : '';
  switch (page.status) {
    case 'ok':
      return null;
    case 'needs_connect':
      return (
        <Banner tone="warn" title="Not connected">
          {page.message}
          <button
            type="button"
            className="dc-text-btn"
            onClick={() => void registry.runCommand('settings.open')}
          >
            Open settings
          </button>
        </Banner>
      );
    case 'rate_limited':
      return (
        <Banner tone="warn" title="Rate limited">
          {page.message}
          {remaining !== null && remaining > 0 ? ` Resets in ${remaining}s.` : ''}
          {cached}
          <button
            type="button"
            className="dc-text-btn"
            disabled={remaining !== null && remaining > 0}
            onClick={() => void load(source, kind, { fresh: true })}
          >
            Retry
          </button>
        </Banner>
      );
    case 'degraded':
      return (
        <Banner tone="info" title="Partial results">
          <Linkified text={page.message ?? ''} />
          {cached}
        </Banner>
      );
    default:
      return (
        <Banner tone="fail" title="Upstream error">
          {page.message}
          {cached}
          <button
            type="button"
            className="dc-text-btn"
            onClick={() => void load(source, kind, { fresh: true })}
          >
            Retry
          </button>
        </Banner>
      );
  }
}

/** Seconds left on a rate limit, counted from when this page arrived. */
function useCountdown(page: DiscoverPage): number | null {
  const [arrived] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const total = page.retry_after;
  useEffect(() => {
    if (total === null || total <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [total, page]);
  if (total === null) return null;
  return Math.max(0, Math.ceil(total - (now - arrived) / 1000));
}

/** Plain text with any https URL made clickable (a degraded message's "browse them
 *  at …" link). The global link bridge routes it to the system browser. */
function Linkified({ text }: { text: string }) {
  const parts = text.split(/(https:\/\/[^\s)]+)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith('https://') ? (
          <a key={i} href={part.replace(/\.$/, '')} target="_blank" rel="noreferrer noopener">
            {part}
          </a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function Skeleton() {
  return (
    <ul className="dc-skeleton" aria-label="Loading">
      {Array.from({ length: 8 }, (_, i) => (
        <li key={i} style={{ ['--i' as string]: i }} />
      ))}
    </ul>
  );
}
