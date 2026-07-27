/**
 * The search module's control surface: which engines can answer, and what this node
 * has crawled for itself.
 *
 * Deliberately not a search box — the agent is the search UI, and a second one here
 * would be a worse browser. What isn't otherwise visible anywhere is *why* a search
 * behaved the way it did: which providers were actually queried, which are sitting
 * idle for want of a key, and how much of the local index exists. That's what this
 * answers.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { subscribeChannel, type WsMessage } from '../../ws';
import {
  clearSearchCache,
  crawlStatus,
  listProviders,
  startCrawl,
  type CrawlProgress,
  type CrawlSeed,
  type CrawlStatus,
  type ProvidersResponse,
} from './api';

function relative(iso?: string | null): string {
  if (!iso) return 'never';
  const then = Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return iso;
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function SearchPanel() {
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [crawl, setCrawl] = useState<CrawlStatus | null>(null);
  const [progress, setProgress] = useState<Record<string, CrawlProgress>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([listProviders(), crawlStatus()]);
      setProviders(p);
      setCrawl(c);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(
    () =>
      subscribeChannel('crawl', (msg: WsMessage) => {
        const data = msg.data as Record<string, unknown>;
        if (msg.event === 'progress') {
          const stats = data as unknown as CrawlProgress;
          setProgress((prev) => ({ ...prev, [stats.seed_id]: stats }));
        } else if (msg.event === 'seed') {
          // Full snapshots, upserted by id — ordering doesn't matter and a dropped
          // frame self-heals on the next one.
          const seed = data as unknown as CrawlSeed;
          setCrawl((prev) =>
            prev ? { ...prev, seeds: prev.seeds.map((s) => (s.id === seed.id ? seed : s)) } : prev,
          );
        }
      }),
    [],
  );

  const runCrawl = useCallback(
    async (seedId?: string) => {
      setBusy(true);
      try {
        await startCrawl(seedId);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const active = useMemo(() => new Set(providers?.active ?? []), [providers]);

  return (
    <div className="search-panel" style={{ padding: '0.6rem', overflowY: 'auto' }}>
      {error && (
        <div className="io-status-blocked" style={{ marginBottom: '0.5rem' }}>
          {error}
        </div>
      )}

      <h3 style={{ margin: '0 0 0.35rem' }}>Engines</h3>
      <p className="dashboard-hint" style={{ margin: '0 0 0.5rem' }}>
        Search fans out across every engine marked <strong>on</strong> and fuses the rankings. Keys
        live in the Web Search connector, never in settings.
      </p>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85em' }}>
        <tbody>
          {(providers?.providers ?? []).map((p) => (
            <tr key={p.id}>
              <td style={{ padding: '0.15rem 0' }}>{p.label}</td>
              <td style={{ color: 'var(--text-dim)' }}>
                {p.configured ? (
                  active.has(p.id) ? (
                    'on'
                  ) : (
                    'ready (fallback)'
                  )
                ) : (
                  <span title={p.reason}>{p.needs_key ? 'no key' : 'not set up'}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ margin: '0.9rem 0 0.35rem' }}>Local index</h3>
      {crawl && (
        <p className="dashboard-hint" style={{ margin: '0 0 0.5rem' }}>
          {crawl.index.docs.toLocaleString()} chunks
          {crawl.index.embed_model ? ` · ${crawl.index.embed_model}` : ''}
          {crawl.index.reindex_needed ? ' · needs a re-crawl' : ''}
        </p>
      )}
      <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <button type="button" disabled={busy} onClick={() => void runCrawl()}>
          Crawl everything due
        </button>
        <button type="button" disabled={busy} onClick={() => void clearSearchCache()}>
          Clear cache
        </button>
      </div>
      {/* Crawling holds the shared task queue, so saying so up front beats leaving
          someone to wonder why their library ingest stalled. */}
      <p className="dashboard-hint" style={{ margin: '0 0 0.5rem' }}>
        A crawl runs for minutes and holds the background task queue while it does.
      </p>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85em' }}>
        <tbody>
          {(crawl?.seeds ?? []).map((seed) => {
            const live = progress[seed.id];
            return (
              <tr key={seed.id} style={{ opacity: seed.enabled ? 1 : 0.5 }}>
                <td style={{ padding: '0.15rem 0' }} title={seed.last_error ?? ''}>
                  {seed.label}
                </td>
                <td style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
                  {live
                    ? `${live.indexed} new · ${live.unchanged + live.not_modified} same`
                    : `${seed.pages} pages · ${relative(seed.last_crawled_at)}`}
                </td>
                <td style={{ width: 60, textAlign: 'right' }}>
                  <button type="button" disabled={busy} onClick={() => void runCrawl(seed.id)}>
                    crawl
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
