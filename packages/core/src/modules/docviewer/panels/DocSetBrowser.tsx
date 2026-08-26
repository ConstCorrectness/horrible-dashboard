/**
 * Doc viewer pane (`docviewer.browse`) — read a captured documentation set offline,
 * with its own CSS *and* its own JavaScript.
 *
 * ## The sandbox is the security model, and it is one attribute wide
 *
 * The archive frame carries `sandbox="allow-scripts"` and **must never** also carry
 * `allow-same-origin`. Those two together are documented by the HTML spec as
 * defeating the sandbox entirely: the page would run script *in our origin* and
 * could read the dashboard's DOM, its storage and its `/api`. With `allow-scripts`
 * alone the document lands in a unique opaque origin — the tabs, collapsibles and
 * client-side search that make modern docs readable all work, and none of them can
 * see us. The response CSP (`artifacts.store.page_csp`) denies it any network on top
 * of that, so it cannot phone home for anything not inlined at capture time.
 *
 * ## Two consequences of that opacity, both deliberate
 *
 * - **We cannot intercept clicks inside the frame.** Intra-set links were therefore
 *   rewritten at capture time to `/api/docviewer/pages/<id>/content`, so following
 *   one navigates the frame to the sibling archive with no help from this component.
 *   The sidebar's own selection uses the same URLs, so the two agree.
 * - **Out-of-scope links are inert.** No `allow-top-navigation`, so a link to the
 *   open web does nothing when clicked. That is the intent: the way out of an
 *   archive is the "live" button, which opens the real page in the browser pane.
 *
 * Params: `{ setId?: string; pageId?: string }`. Multi-instance — two sets side by
 * side is a normal way to read documentation.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { usePaneParams } from '../../../panes';
import { registry } from '../../../registry';
import { toastsStore } from '../../../toasts';
import { subscribeChannel, type WsMessage } from '../../../ws';
import {
  deleteSet,
  listPages,
  listSets,
  pageContentUrl,
  recrawlSet,
  searchSet,
  type CrawlProgress,
  type DocPage,
  type DocSet,
  type SearchHit,
} from '../api';
import { BackIcon, ExternalIcon, PlusIcon, RefreshIcon, SearchIcon, TrashIcon } from '../icons';
import { NewDocSet } from './NewDocSet';

/** Entrance delay for row `i`, capped so a long list still lands in ~240ms. */
function stagger(i: number): string {
  return `${Math.min(i * 12, 240)}ms`;
}

const CONTROL_HEIGHT = 30;

const controlStyle: React.CSSProperties = {
  height: CONTROL_HEIGHT,
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border)',
  background: 'var(--bg-inset)',
  color: 'var(--text)',
  padding: '0 var(--space-5)',
  fontSize: '0.8125rem',
  fontFamily: 'inherit',
};

const ghostButtonStyle: React.CSSProperties = {
  ...controlStyle,
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-3)',
  background: 'transparent',
  color: 'var(--text-dim)',
  cursor: 'pointer',
};

const sectionLabelStyle: React.CSSProperties = {
  fontSize: '0.625rem',
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-faint)',
  padding: 'var(--space-5) var(--space-5) var(--space-3)',
};

const monoStyle: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: '0.6875rem',
  color: 'var(--text-faint)',
};

export function DocSetBrowser() {
  const params = usePaneParams();
  const initialSetId = typeof params.setId === 'string' ? params.setId : null;
  const initialPageId = typeof params.pageId === 'string' ? params.pageId : null;

  const [sets, setSets] = useState<DocSet[] | null>(null);
  const [setId, setSetId] = useState<string | null>(initialSetId);
  const [pages, setPages] = useState<DocPage[]>([]);
  const [pageId, setPageId] = useState<string | null>(initialPageId);
  const [progress, setProgress] = useState<CrawlProgress | null>(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshSets = useCallback(async () => {
    const res = await listSets();
    setSets(res.sets);
    setSetId((current) => current ?? res.sets[0]?.id ?? null);
  }, []);

  useEffect(() => {
    refreshSets().catch((err: unknown) =>
      setError(err instanceof Error ? err.message : String(err)),
    );
  }, [refreshSets]);

  const refreshPages = useCallback(async (id: string) => {
    const res = await listPages(id);
    setPages(res.pages);
  }, []);

  useEffect(() => {
    if (!setId) {
      setPages([]);
      return;
    }
    setHits(null);
    refreshPages(setId).catch((err: unknown) =>
      setError(err instanceof Error ? err.message : String(err)),
    );
  }, [setId, refreshPages]);

  // Live crawl progress. A crawl runs detached on the backend for minutes, so this
  // channel is the only thing that fills the tree while it happens.
  useEffect(() => {
    return subscribeChannel('docviewer', (msg: WsMessage) => {
      const data = msg.data as Record<string, unknown> | undefined;
      if (!data) return;
      if (msg.event === 'progress') {
        const next = data as unknown as CrawlProgress;
        if (next.set_id === setId) setProgress(next.status === 'crawling' ? next : null);
      } else if (msg.event === 'page') {
        const page = data as unknown as DocPage;
        if (page.set_id !== setId) return;
        setPages((current) => {
          const index = current.findIndex((p) => p.id === page.id);
          if (index < 0) return [...current, page];
          const copy = current.slice();
          copy[index] = page;
          return copy;
        });
      } else if (msg.event === 'set') {
        const doc = data as unknown as DocSet;
        setSets((current) => (current ? current.map((s) => (s.id === doc.id ? doc : s)) : current));
      }
    });
  }, [setId]);

  const activeSet = useMemo(() => sets?.find((s) => s.id === setId) ?? null, [sets, setId]);
  const captured = useMemo(() => pages.filter((p) => p.status === 'captured'), [pages]);
  const activePage = useMemo(
    () => captured.find((p) => p.id === pageId) ?? captured[0] ?? null,
    [captured, pageId],
  );

  const runSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!setId || !trimmed) {
      setHits(null);
      return;
    }
    setSearching(true);
    try {
      const res = await searchSet(setId, trimmed);
      setHits(res.hits);
    } catch (err) {
      toastsStore.add('warning', 'Search failed', err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  }, [query, setId]);

  if (creating) {
    return (
      <NewDocSet
        onCancel={() => setCreating(false)}
        onCreated={(doc) => {
          setCreating(false);
          setSets((current) => (current ? [doc, ...current] : [doc]));
          setSetId(doc.id);
          setPageId(null);
        }}
      />
    );
  }

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0, background: 'var(--bg)' }}>
      <Sidebar
        sets={sets}
        setId={setId}
        activeSet={activeSet}
        pages={captured}
        pageId={activePage?.id ?? null}
        progress={progress}
        query={query}
        hits={hits}
        searching={searching}
        error={error}
        onQuery={setQuery}
        onSearch={runSearch}
        onSelectSet={(id) => {
          setSetId(id);
          setPageId(null);
          setProgress(null);
        }}
        onSelectPage={setPageId}
        onNew={() => setCreating(true)}
        onRecrawl={async () => {
          if (!setId) return;
          try {
            await recrawlSet(setId);
            toastsStore.add('info', 'Re-crawling', activeSet?.title ?? setId);
          } catch (err) {
            toastsStore.add(
              'warning',
              'Re-crawl failed',
              err instanceof Error ? err.message : String(err),
            );
          }
        }}
        onDelete={async () => {
          if (!setId) return;
          try {
            await deleteSet(setId);
            setSetId(null);
            setPageId(null);
            await refreshSets();
          } catch (err) {
            toastsStore.add(
              'warning',
              'Delete failed',
              err instanceof Error ? err.message : String(err),
            );
          }
        }}
      />
      <ArchiveFrame page={activePage} setTitle={activeSet?.title ?? null} />
    </div>
  );
}

interface SidebarProps {
  sets: DocSet[] | null;
  setId: string | null;
  activeSet: DocSet | null;
  pages: DocPage[];
  pageId: string | null;
  progress: CrawlProgress | null;
  query: string;
  hits: SearchHit[] | null;
  searching: boolean;
  error: string | null;
  onQuery: (value: string) => void;
  onSearch: () => void;
  onSelectSet: (id: string) => void;
  onSelectPage: (id: string) => void;
  onNew: () => void;
  onRecrawl: () => void;
  onDelete: () => void;
}

function Sidebar(props: SidebarProps) {
  const { sets, setId, activeSet, pages, pageId, progress, query, hits } = props;

  return (
    <div
      style={{
        width: 280,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        borderRight: '1px solid var(--border)',
        background: 'var(--bg-raised)',
        // A 2px accent edge rather than a glowing perimeter — structure, not decoration.
        borderTop: '2px solid var(--accent)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          padding: 'var(--space-4) var(--space-5)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <input
          value={query}
          placeholder={activeSet ? `Search ${activeSet.title}` : 'Search'}
          disabled={!setId}
          onChange={(e) => props.onQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') props.onSearch();
          }}
          style={{ ...controlStyle, flex: 1, minWidth: 0 }}
        />
        <button
          type="button"
          onClick={props.onSearch}
          disabled={!setId || props.searching}
          title="Search this set"
          style={{ ...ghostButtonStyle, padding: '0 var(--space-4)' }}
        >
          <SearchIcon />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {hits ? (
          <SearchResults
            hits={hits}
            onSelect={props.onSelectPage}
            onClear={() => props.onQuery('')}
          />
        ) : (
          <>
            <div style={sectionLabelStyle}>Doc sets</div>
            {props.error && (
              <div
                style={{ padding: '0 var(--space-5)', color: 'var(--danger)', fontSize: '0.75rem' }}
              >
                {props.error}
              </div>
            )}
            {sets === null && <Placeholder text="loading" />}
            {sets?.length === 0 && <Placeholder text="no sets captured yet" />}
            {sets?.map((doc, i) => (
              <SetRow
                key={doc.id}
                doc={doc}
                active={doc.id === setId}
                index={i}
                onSelect={() => props.onSelectSet(doc.id)}
              />
            ))}

            {activeSet && (
              <>
                <div style={sectionLabelStyle}>
                  Pages
                  <span style={{ ...monoStyle, marginLeft: 'var(--space-4)' }}>{pages.length}</span>
                </div>
                {progress && <CrawlBar progress={progress} />}
                {pages.length === 0 && !progress && <Placeholder text="no pages captured" />}
                {pages.map((page, i) => (
                  <PageRow
                    key={page.id}
                    page={page}
                    active={page.id === pageId}
                    index={i}
                    onSelect={() => props.onSelectPage(page.id)}
                  />
                ))}
              </>
            )}
          </>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          gap: 'var(--space-3)',
          padding: 'var(--space-4) var(--space-5)',
          borderTop: '1px solid var(--border)',
        }}
      >
        <button type="button" onClick={props.onNew} style={{ ...ghostButtonStyle, flex: 1 }}>
          <PlusIcon />
          New set
        </button>
        <button
          type="button"
          onClick={props.onRecrawl}
          disabled={!setId}
          title="Capture this set again"
          style={{ ...ghostButtonStyle, padding: '0 var(--space-4)' }}
        >
          <RefreshIcon />
        </button>
        <button
          type="button"
          onClick={props.onDelete}
          disabled={!setId}
          title="Delete this set, its archives and its library sources"
          style={{ ...ghostButtonStyle, padding: '0 var(--space-4)' }}
        >
          <TrashIcon />
        </button>
      </div>
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return <div style={{ padding: 'var(--space-3) var(--space-5)', ...monoStyle }}>{text}</div>;
}

function CrawlBar({ progress }: { progress: CrawlProgress }) {
  return (
    <div style={{ padding: 'var(--space-3) var(--space-5)' }}>
      <div style={{ ...monoStyle, color: 'var(--text-dim)' }}>
        capturing {progress.captured}
        {progress.failed > 0 ? ` · ${progress.failed} failed` : ''} · {progress.queued} queued
      </div>
      {progress.current_url && (
        <div
          style={{
            ...monoStyle,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={progress.current_url}
        >
          {progress.current_url}
        </div>
      )}
    </div>
  );
}

function SetRow(props: { doc: DocSet; active: boolean; index: number; onSelect: () => void }) {
  const { doc, active } = props;
  return (
    <button
      type="button"
      onClick={props.onSelect}
      style={{
        display: 'block',
        width: '100%',
        textAlign: 'left',
        border: 0,
        borderLeft: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
        background: active ? 'var(--bg-hover)' : 'transparent',
        color: active ? 'var(--text-strong)' : 'var(--text)',
        padding: 'var(--space-3) var(--space-5)',
        cursor: 'pointer',
        animation: `docviewer-rise var(--dur-base) var(--ease-entrance) ${stagger(props.index)} both`,
      }}
    >
      <div style={{ fontSize: '0.8125rem', fontWeight: active ? 600 : 400 }}>{doc.title}</div>
      <div style={monoStyle}>
        {doc.page_count} pages
        {doc.status !== 'ready' ? ` · ${doc.status}` : ''}
      </div>
    </button>
  );
}

function PageRow(props: { page: DocPage; active: boolean; index: number; onSelect: () => void }) {
  const { page, active } = props;
  return (
    <button
      type="button"
      onClick={props.onSelect}
      title={page.url}
      style={{
        display: 'block',
        width: '100%',
        textAlign: 'left',
        border: 0,
        borderLeft: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
        background: active ? 'var(--bg-hover)' : 'transparent',
        color: active ? 'var(--text-strong)' : 'var(--text-dim)',
        // Depth is the tree: an indent per crawl level, capped so a deep site does
        // not push its titles off the edge.
        padding: `var(--space-2) var(--space-5) var(--space-2) calc(var(--space-5) + ${Math.min(page.depth, 4) * 10}px)`,
        fontSize: '0.75rem',
        cursor: 'pointer',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        animation: `docviewer-rise var(--dur-fast) var(--ease-entrance) ${stagger(props.index)} both`,
      }}
    >
      {page.title}
    </button>
  );
}

function SearchResults(props: {
  hits: SearchHit[];
  onSelect: (pageId: string) => void;
  onClear: () => void;
}) {
  return (
    <>
      <div
        style={{
          ...sectionLabelStyle,
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-4)',
        }}
      >
        Results
        <button
          type="button"
          onClick={props.onClear}
          style={{
            ...ghostButtonStyle,
            height: 20,
            padding: '0 var(--space-3)',
            marginLeft: 'auto',
            fontSize: '0.625rem',
            letterSpacing: '0.14em',
          }}
        >
          <BackIcon size={11} />
          Tree
        </button>
      </div>
      {props.hits.length === 0 && <Placeholder text="no matches" />}
      {props.hits.map((hit, i) => (
        <button
          key={`${hit.page_id}-${i}`}
          type="button"
          onClick={() => hit.page_id && props.onSelect(hit.page_id)}
          style={{
            display: 'block',
            width: '100%',
            textAlign: 'left',
            border: 0,
            borderLeft: '2px solid transparent',
            background: 'transparent',
            color: 'var(--text)',
            padding: 'var(--space-3) var(--space-5)',
            cursor: 'pointer',
            animation: `docviewer-rise var(--dur-base) var(--ease-entrance) ${stagger(i)} both`,
          }}
        >
          <div style={{ fontSize: '0.75rem', fontWeight: 600 }}>{hit.title}</div>
          <div
            style={{
              fontSize: '0.6875rem',
              color: 'var(--text-dim)',
              display: '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {hit.snippet}
          </div>
        </button>
      ))}
    </>
  );
}

function ArchiveFrame({ page, setTitle }: { page: DocPage | null; setTitle: string | null }) {
  const frameRef = useRef<HTMLIFrameElement>(null);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-4)',
          padding: 'var(--space-4) var(--space-5)',
          borderBottom: '1px solid var(--border)',
          minWidth: 0,
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: '0.8125rem',
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {page?.title ?? setTitle ?? 'Documentation'}
          </div>
          {page && (
            <div
              style={{
                ...monoStyle,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={page.url}
            >
              {page.url}
            </div>
          )}
        </div>
        {page && (
          <button
            type="button"
            onClick={() => registry.openPanel('browser.view', { params: { url: page.url } })}
            title={`Open the live page: ${page.url}`}
            style={ghostButtonStyle}
          >
            <ExternalIcon />
            Live
          </button>
        )}
      </div>
      {page ? (
        <iframe
          ref={frameRef}
          key={page.id}
          src={pageContentUrl(page.id)}
          // `allow-scripts` and NOT `allow-same-origin`. Adding the second would put
          // this third-party page in our origin and void the sandbox entirely — see
          // the module docstring. The archive's own JS runs; it just runs nowhere
          // near us.
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          title={page.title}
          style={{ flex: 1, border: 0, background: 'var(--bg-raised)' }}
        />
      ) : (
        <div
          style={{
            flex: 1,
            display: 'grid',
            placeItems: 'center',
            color: 'var(--text-faint)',
            fontSize: '0.8125rem',
          }}
        >
          Capture a documentation set to read it here.
        </div>
      )}
    </div>
  );
}
