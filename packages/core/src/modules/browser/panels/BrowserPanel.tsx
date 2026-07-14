import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from 'react';

import { hasCapability } from '../../../capabilities';
import { usePaneParams } from '../../../panes';
import { useSetting } from '../../../settings';
import { windowControl } from '../../../window';
import {
  addBookmark,
  clearHistory,
  engineStatus,
  listBookmarks,
  listHistory,
  readerMode,
  recordHistory,
  removeBookmark,
  type Bookmark,
  type HistoryEntry,
  type ReaderArticle,
} from '../api';
import { sendInput, stopSession } from '../session';
import { FullBrowserView } from './FullBrowserView';

/**
 * The embedded browser pane. Renders a page inline via `<iframe>` (works in both
 * the web and desktop builds) with a URL bar and per-pane back/forward history —
 * the iframe is cross-origin, so navigation history is tracked here, not read
 * from the frame.
 *
 * Most large sites send `X-Frame-Options`/CSP `frame-ancestors` and refuse to be
 * framed; iframe-blocking is not reliably detectable cross-origin, so instead of
 * guessing we always offer manual escapes: **Reader** (server-side extract of the
 * page text, `/api/browser/read`), **Pop out** to a real native window (desktop
 * only — gated on `browser.nativeWindow`), and **Open tab** (`window.open`).
 * Bookmarks + history persist server-side. See docs/modules/browser.mdx.
 */

// The active pane's URL-bar focuser, for the `browser.focusUrlBar` command. Only
// one browser pane is "active" (last focused), which is what a global shortcut wants.
let activeUrlBarFocus: (() => void) | null = null;
export function focusActiveUrlBar(): void {
  activeUrlBarFocus?.();
}

/** Turn URL-bar text into a navigable URL: bare host → https://, else a search. */
function normalizeUrl(raw: string): string {
  const s = raw.trim();
  if (!s) return '';
  if (/^https?:\/\//i.test(s)) return s;
  const looksLikeHost =
    !/\s/.test(s) && (/\.[a-z]{2,}(\/|$|:|\?)/i.test(s) || s.startsWith('localhost'));
  return looksLikeHost ? `https://${s}` : `https://duckduckgo.com/?q=${encodeURIComponent(s)}`;
}

interface Nav {
  stack: string[];
  idx: number;
}

const btn: CSSProperties = {
  padding: '0.15rem 0.45rem',
  fontSize: '0.8rem',
  background: 'transparent',
  border: '1px solid var(--border)',
  borderRadius: 4,
  color: 'var(--text)',
  cursor: 'pointer',
};

export function BrowserPanel() {
  const params = usePaneParams();
  const homePage = (useSetting<string>('browser.homePage') ?? '').trim();
  const readerDefault = useSetting<boolean>('browser.readerModeDefault') ?? false;
  const enginePref = useSetting<string>('browser.engine') ?? 'auto';

  // Whether to run the real backend Chromium engine ("full mode") vs the light
  // iframe. In `auto` we use the engine only when the backend reports it enabled;
  // `full`/`iframe` force it. Resolved once on mount (the gate is process-wide).
  const [engineOn, setEngineOn] = useState(false);
  useEffect(() => {
    if (enginePref === 'iframe') {
      setEngineOn(false);
      return;
    }
    engineStatus()
      .then((s) => setEngineOn(enginePref === 'full' ? true : s.enabled))
      .catch(() => setEngineOn(false));
  }, [enginePref]);
  const useFull = enginePref !== 'iframe' && engineOn;

  const initialUrl = typeof params.url === 'string' ? params.url : '';
  const [nav, setNav] = useState<Nav>(() =>
    initialUrl ? { stack: [initialUrl], idx: 0 } : { stack: [], idx: -1 },
  );
  // Full-mode nav: the engine owns real back/forward history, so we track only the
  // target URL to (re)load and a sequence to re-issue navigation (also for reload),
  // plus the live URL/title the backend reports from the rendered page.
  const [fullTarget, setFullTarget] = useState(initialUrl);
  const [navSeq, setNavSeq] = useState(0);
  const [liveMeta, setLiveMeta] = useState<{ url: string; title: string } | null>(null);

  const current = useFull ? liveMeta?.url || fullTarget : nav.idx >= 0 ? nav.stack[nav.idx] : '';
  const canBack = useFull ? true : nav.idx > 0;
  const canForward = useFull ? true : nav.idx >= 0 && nav.idx < nav.stack.length - 1;

  const [input, setInput] = useState(initialUrl);
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [reader, setReader] = useState<ReaderArticle | null>(null);
  const [readerBusy, setReaderBusy] = useState(false);
  const [readerError, setReaderError] = useState<string | null>(null);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  const urlRef = useRef<HTMLInputElement>(null);
  const canPopOut = hasCapability('browser.nativeWindow');
  const bookmarked = current !== '' && bookmarks.some((b) => b.url === current);

  const go = useCallback(
    (raw: string) => {
      const url = normalizeUrl(raw);
      if (!url) return;
      if (useFull) {
        // The engine owns history; just point it at the new URL and re-issue nav.
        setFullTarget(url);
        setNavSeq((s) => s + 1);
        setInput(url);
        recordHistory(url, url).catch(() => {});
        return;
      }
      setNav((n) => {
        const stack = [...n.stack.slice(0, n.idx + 1), url];
        return { stack, idx: stack.length - 1 };
      });
    },
    [useFull],
  );

  // Release the backend Chromium when the pane unmounts.
  useEffect(() => (useFull ? () => stopSession() : undefined), [useFull]);

  // Home falls back to a blank start page when unset (default).
  const homeUrl = homePage || initialUrl;

  const refreshBookmarks = useCallback(() => {
    listBookmarks()
      .then((r) => setBookmarks(r.bookmarks))
      .catch(() => setBookmarks([]));
  }, []);

  useEffect(() => {
    refreshBookmarks();
  }, [refreshBookmarks]);

  const loadReader = useCallback((url: string) => {
    setReaderBusy(true);
    setReaderError(null);
    readerMode(url)
      .then((a) => setReader(a))
      .catch((e: Error) => setReaderError(e.message))
      .finally(() => setReaderBusy(false));
  }, []);

  // On navigation: sync the URL bar, reset the frame view, record history, and —
  // if reader-mode is the default — fetch the readable version.
  useEffect(() => {
    if (!current) return;
    setInput(current);
    if (!useFull) setLoading(true); // full mode streams frames; no iframe onLoad
    setReader(null);
    setReaderError(null);
    recordHistory(current, liveMeta?.title || current).catch(() => {});
    if (readerDefault) loadReader(current);
    // Re-runs on navigation (current) and reload; readerDefault/loadReader are
    // read once per run by design, not reactive deps.
  }, [current, reloadKey, readerDefault, loadReader, useFull, liveMeta?.title]);

  // Register this pane as the focus target for the global focus-url-bar command.
  useEffect(() => {
    const focus = () => {
      urlRef.current?.focus();
      urlRef.current?.select();
    };
    activeUrlBarFocus = focus;
    return () => {
      if (activeUrlBarFocus === focus) activeUrlBarFocus = null;
    };
  }, []);

  const toggleBookmark = () => {
    if (!current) return;
    const existing = bookmarks.find((b) => b.url === current);
    const op = existing
      ? removeBookmark(existing.id)
      : addBookmark(current, reader?.title || current);
    op.then(refreshBookmarks).catch(() => {});
  };

  const openHistory = () => {
    const next = !showHistory;
    setShowHistory(next);
    if (next)
      listHistory()
        .then((r) => setHistory(r.entries))
        .catch(() => setHistory([]));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(input);
  };

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', height: '100%' }}
      onFocusCapture={() => {
        activeUrlBarFocus = () => {
          urlRef.current?.focus();
          urlRef.current?.select();
        };
      }}
    >
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.3rem',
          padding: '0.3rem 0.4rem',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <button
          type="button"
          style={btn}
          disabled={!canBack}
          title="Back"
          onClick={() => (useFull ? sendInput('back') : setNav((n) => ({ ...n, idx: n.idx - 1 })))}
        >
          ‹
        </button>
        <button
          type="button"
          style={btn}
          disabled={!canForward}
          title="Forward"
          onClick={() =>
            useFull ? sendInput('forward') : setNav((n) => ({ ...n, idx: n.idx + 1 }))
          }
        >
          ›
        </button>
        <button
          type="button"
          style={btn}
          disabled={!current}
          title="Reload"
          onClick={() => (useFull ? sendInput('reload') : setReloadKey((k) => k + 1))}
        >
          ⟳
        </button>
        <button
          type="button"
          style={btn}
          disabled={!homeUrl}
          title="Home"
          onClick={() => homeUrl && go(homeUrl)}
        >
          ⌂
        </button>
        <form onSubmit={submit} style={{ flex: 1, display: 'flex' }}>
          <input
            ref={urlRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            spellCheck={false}
            placeholder="Enter a URL or search…"
            style={{
              flex: 1,
              padding: '0.2rem 0.5rem',
              fontSize: '0.8rem',
              border: '1px solid var(--border)',
              borderRadius: 4,
              background: 'var(--bg, #1e1e1e)',
              color: 'var(--text)',
            }}
          />
        </form>
        <button
          type="button"
          style={{ ...btn, color: bookmarked ? 'var(--accent, #6ea8fe)' : undefined }}
          disabled={!current}
          title="Bookmark"
          onClick={toggleBookmark}
        >
          {bookmarked ? '★' : '☆'}
        </button>
        <button
          type="button"
          style={{ ...btn, color: reader ? 'var(--accent, #6ea8fe)' : undefined }}
          disabled={!current}
          title="Reader mode"
          onClick={() => (reader ? setReader(null) : loadReader(current))}
        >
          ▤
        </button>
        <button type="button" style={btn} title="History" onClick={openHistory}>
          🕘
        </button>
        {canPopOut ? (
          <button
            type="button"
            style={btn}
            disabled={!current}
            title="Open in native window"
            onClick={() => current && windowControl()?.openBrowserWindow(current)}
          >
            ⧉
          </button>
        ) : (
          <button
            type="button"
            style={btn}
            disabled={!current}
            title="Open in new tab"
            onClick={() => current && window.open(current, '_blank', 'noopener')}
          >
            ⧉
          </button>
        )}
      </div>

      {/* Bookmarks strip */}
      {bookmarks.length > 0 && (
        <div
          style={{
            display: 'flex',
            gap: '0.3rem',
            padding: '0.25rem 0.4rem',
            borderBottom: '1px solid var(--border)',
            overflowX: 'auto',
          }}
        >
          {bookmarks.map((b) => (
            <button
              key={b.id}
              type="button"
              style={{ ...btn, whiteSpace: 'nowrap', fontSize: '0.72rem' }}
              title={b.url}
              onClick={() => go(b.url)}
            >
              {b.title || b.url}
            </button>
          ))}
        </div>
      )}

      {/* History dropdown */}
      {showHistory && (
        <div
          style={{
            borderBottom: '1px solid var(--border)',
            maxHeight: '40%',
            overflowY: 'auto',
            fontSize: '0.78rem',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              padding: '0.3rem 0.5rem',
              color: 'var(--text-dim)',
            }}
          >
            <span>Recent</span>
            <button
              type="button"
              style={btn}
              onClick={() =>
                clearHistory()
                  .then(() => setHistory([]))
                  .catch(() => {})
              }
            >
              Clear
            </button>
          </div>
          {history.length === 0 ? (
            <div style={{ padding: '0.3rem 0.5rem', color: 'var(--text-dim)' }}>
              No history yet.
            </div>
          ) : (
            history.map((h) => (
              <button
                key={h.id}
                type="button"
                style={{
                  ...btn,
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  border: 'none',
                  borderRadius: 0,
                }}
                title={h.url}
                onClick={() => {
                  setShowHistory(false);
                  go(h.url);
                }}
              >
                {h.title || h.url}
              </button>
            ))
          )}
        </div>
      )}

      {/* Content: reader view, or the iframe, or the start page */}
      <div style={{ flex: 1, position: 'relative', overflow: 'auto' }}>
        {reader ? (
          <article style={{ maxWidth: 720, margin: '0 auto', padding: '1.5rem', lineHeight: 1.6 }}>
            <h1 style={{ fontSize: '1.4rem' }}>{reader.title}</h1>
            {reader.author && (
              <div style={{ color: 'var(--text-dim)', marginBottom: '1rem' }}>{reader.author}</div>
            )}
            <div style={{ whiteSpace: 'pre-wrap' }}>{reader.text}</div>
          </article>
        ) : readerBusy ? (
          <div style={{ padding: '1rem', color: 'var(--text-dim)' }}>
            Fetching readable version…
          </div>
        ) : readerError ? (
          <div style={{ padding: '1rem', color: 'var(--danger, #f87171)' }}>
            Reader mode failed: {readerError}
          </div>
        ) : useFull && current ? (
          <FullBrowserView url={fullTarget} navSeq={navSeq} onMeta={setLiveMeta} />
        ) : current ? (
          <iframe
            key={`${reloadKey}:${current}`}
            src={current}
            title="Embedded browser"
            onLoad={() => setLoading(false)}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
            style={{ width: '100%', height: '100%', border: 'none', background: '#fff' }}
          />
        ) : (
          <div style={{ padding: '2rem', color: 'var(--text-dim)', textAlign: 'center' }}>
            <div style={{ fontSize: '2rem' }}>🌐</div>
            <p>
              Type a URL above to start browsing. Some sites block embedding — use ▤ Reader or ⧉ to
              open them.
            </p>
          </div>
        )}
        {loading && current && !reader && (
          <div
            style={{
              position: 'absolute',
              top: 6,
              right: 10,
              fontSize: '0.7rem',
              color: 'var(--text-dim)',
            }}
          >
            loading…
          </div>
        )}
      </div>
    </div>
  );
}
