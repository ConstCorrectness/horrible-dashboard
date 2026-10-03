/**
 * The posts database: a site's posts as a **table** (sortable, filterable) or a
 * **board** grouped by status, after Notion's "a blog is a database of pages".
 *
 * There is no database. Every row is a file and every column a frontmatter key, read
 * by the backend's page listing; moving a card between columns rewrites that one
 * line of that one file (`posts.ts`). So the board and a hand edit of the file can
 * never disagree — the next `page.changed` re-lists either way.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { subscribeChannel } from '../../../ws';
import { listPages, SCRIVE_CHANNEL, type PageChanged, type PageMeta } from '../api';
import { SortDownIcon, SortUpIcon } from '../icons';
import { openScrivePage } from '../open';
import { setPageField } from '../posts';
import { useCurrentSite } from '../state';
import '../scrive.css';

type View = 'table' | 'board';
type SortKey = 'title' | 'status' | 'date' | 'updated_at';

/** The board's columns, in workflow order; any other status gets a column too. */
const STATUS_ORDER = ['draft', 'review', 'scheduled', 'published'];
const STAGGER_MS = 18;
const STAGGER_CAP = 12;
const VIEW_KEY = 'scrive.postsView';

function rememberedView(): View {
  try {
    return globalThis.localStorage?.getItem(VIEW_KEY) === 'board' ? 'board' : 'table';
  } catch {
    return 'table';
  }
}

export function PostsPanel() {
  const site = useCurrentSite();
  const [posts, setPosts] = useState<PageMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [view, setViewState] = useState<View>(rememberedView);
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'date', desc: true });
  /** Path → status while a move is in flight, so the card lands at once. */
  const [moving, setMoving] = useState<Record<string, string>>({});

  const setView = (next: View) => {
    setViewState(next);
    try {
      globalThis.localStorage?.setItem(VIEW_KEY, next);
    } catch {
      // A remembered view is a convenience.
    }
  };

  const refresh = useCallback(async () => {
    if (!site) return;
    try {
      setPosts((await listPages(site)).filter((p) => p.kind === 'post'));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [site]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'page.changed' || (msg.data as PageChanged).site !== site) return;
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => void refresh(), 250);
      }),
    [site, refresh],
  );

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const rows = posts
      .map((p) => (moving[p.path] ? { ...p, status: moving[p.path] } : p))
      .filter(
        (p) =>
          !q ||
          p.title.toLowerCase().includes(q) ||
          p.tags.some((t) => t.toLowerCase().includes(q)) ||
          p.status.toLowerCase().includes(q),
      );
    const dir = sort.desc ? -1 : 1;
    return rows.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      return (av < bv ? -1 : av > bv ? 1 : 0) * dir;
    });
  }, [posts, filter, sort, moving]);

  const move = useCallback(
    async (path: string, status: string) => {
      setMoving((m) => ({ ...m, [path]: status }));
      try {
        await setPageField(site ?? '', path, 'status', status);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setMoving((m) => {
          const next = { ...m };
          delete next[path];
          return next;
        });
      }
    },
    [site, refresh],
  );

  if (!site) {
    return (
      <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>
        Pick a site in the Scrive pane first.
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        fontSize: 'var(--fs-body)',
      }}
    >
      <header
        className="scrive-bar"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 'var(--space-2) var(--space-3)',
          padding: 'var(--space-2) var(--space-3)',
        }}
      >
        <span className="scrive-head" style={{ color: 'var(--text-strong)' }}>
          Posts
        </span>
        <span className="scrive-meta">
          {site} · {shown.length}/{posts.length}
        </span>
        <input
          type="search"
          aria-label="Filter posts"
          placeholder="Filter by title, tag, status"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: '1 1 12rem', minWidth: 0, padding: '0 0.6rem' }}
        />
        <div className="scrive-seg" role="group" aria-label="View">
          {(['table', 'board'] as View[]).map((v) => (
            <button
              key={v}
              type="button"
              className="scrive-seg-btn"
              aria-pressed={view === v}
              onClick={() => setView(v)}
            >
              {v}
            </button>
          ))}
        </div>
      </header>
      {error && (
        <div style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {posts.length === 0 ? (
          <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>
            No posts in this site yet. Start one from the Scrive pane.
          </div>
        ) : view === 'table' ? (
          <PostTable
            rows={shown}
            sort={sort}
            onSort={(key) =>
              setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== 'title' }))
            }
            onOpen={(p) => openScrivePage(site, p.path)}
          />
        ) : (
          <PostBoard rows={shown} onOpen={(p) => openScrivePage(site, p.path)} onMove={move} />
        )}
      </div>
    </div>
  );
}

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'title', label: 'Title' },
  { key: 'status', label: 'Status' },
  { key: 'date', label: 'Date' },
  { key: 'updated_at', label: 'Edited' },
];

function PostTable({
  rows,
  sort,
  onSort,
  onOpen,
}: {
  rows: PageMeta[];
  sort: { key: SortKey; desc: boolean };
  onSort: (key: SortKey) => void;
  onOpen: (p: PageMeta) => void;
}) {
  return (
    <table className="scrive-db">
      <thead>
        <tr>
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              aria-sort={sort.key === c.key ? (sort.desc ? 'descending' : 'ascending') : 'none'}
            >
              <button type="button" className="scrive-db-sort" onClick={() => onSort(c.key)}>
                {c.label}
                {sort.key === c.key &&
                  (sort.desc ? <SortDownIcon size={11} /> : <SortUpIcon size={11} />)}
              </button>
            </th>
          ))}
          <th>
            <span className="scrive-db-sort">Tags</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p, i) => (
          <tr
            key={p.path}
            className="scrive-db-row"
            style={{ animationDelay: `${Math.min(i, STAGGER_CAP) * STAGGER_MS}ms` }}
          >
            <td>
              <button
                type="button"
                className="scrive-db-title"
                onClick={() => onOpen(p)}
                title={p.path}
              >
                {p.title}
              </button>
              {p.description && <div className="scrive-db-desc">{p.description}</div>}
            </td>
            <td>
              {p.status && (
                <span className="scrive-chip" data-status={p.status}>
                  {p.status}
                </span>
              )}
            </td>
            <td className="scrive-meta">{p.date}</td>
            <td className="scrive-meta">
              {new Date(p.updated_at * 1000).toISOString().slice(0, 10)}
            </td>
            <td>
              <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {p.tags.map((t) => (
                  <span key={t} className="scrive-chip">
                    {t}
                  </span>
                ))}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PostBoard({
  rows,
  onOpen,
  onMove,
}: {
  rows: PageMeta[];
  onOpen: (p: PageMeta) => void;
  onMove: (path: string, status: string) => void;
}) {
  const [over, setOver] = useState<string | null>(null);
  const statuses = useMemo(() => {
    const extra = [...new Set(rows.map((r) => r.status || 'draft'))].filter(
      (s) => !STATUS_ORDER.includes(s),
    );
    return [...STATUS_ORDER, ...extra.sort()];
  }, [rows]);
  return (
    <div className="scrive-board">
      {statuses.map((status) => {
        const cards = rows.filter((r) => (r.status || 'draft') === status);
        return (
          <section
            key={status}
            className="scrive-board-col"
            data-over={over === status}
            onDragOver={(e) => {
              if (!e.dataTransfer.types.includes('application/x-scrive-post')) return;
              e.preventDefault();
              setOver(status);
            }}
            onDragLeave={() => setOver((o) => (o === status ? null : o))}
            onDrop={(e) => {
              e.preventDefault();
              setOver(null);
              const path = e.dataTransfer.getData('application/x-scrive-post');
              const card = rows.find((r) => r.path === path);
              if (card && (card.status || 'draft') !== status) onMove(path, status);
            }}
          >
            <div
              className="scrive-head"
              style={{ display: 'flex', justifyContent: 'space-between' }}
            >
              <span>{status}</span>
              <span className="scrive-meta">{cards.length}</span>
            </div>
            {cards.map((p, i) => (
              <button
                key={p.path}
                type="button"
                className="scrive-card"
                draggable
                style={{ animationDelay: `${Math.min(i, STAGGER_CAP) * STAGGER_MS}ms` }}
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/x-scrive-post', p.path);
                  e.dataTransfer.effectAllowed = 'move';
                }}
                onClick={() => onOpen(p)}
                title={p.path}
              >
                <span className="scrive-card-title">{p.title}</span>
                {p.description && <span className="scrive-db-desc">{p.description}</span>}
                <span className="scrive-meta" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {p.date && <span>{p.date}</span>}
                  {p.tags.map((t) => (
                    <span key={t}>#{t}</span>
                  ))}
                </span>
              </button>
            ))}
          </section>
        );
      })}
    </div>
  );
}
