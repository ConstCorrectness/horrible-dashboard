/**
 * Spotlight's empty state: the morning briefing, quick settings, notifications.
 *
 * Not a second overlay. `mod+k` still opens exactly one surface; this is what it
 * shows *before* you type, where it used to show every command in registry order
 * — a list nobody read, since anyone who knew what they wanted typed it. The
 * first keystroke swaps this out for the ordinary results, so nothing a user
 * reached by typing moved.
 *
 * Everything here reads stores that already exist (settings, notifications,
 * theme) or the briefing module's own. Actions go through commands or settings;
 * nothing here owns state beyond "is this save button busy".
 */
import { useEffect, useState, useSyncExternalStore, type CSSProperties } from 'react';
import {
  arxivAbsUrl,
  arxivPdfUrl,
  dismissNotification,
  getNotifications,
  GRAPHICS_QUALITY_SETTING_KEY,
  hasCapability,
  hfPaperUrl,
  IconArrowUp,
  IconCheck,
  IconClose,
  IconComment,
  IconDot,
  IconExternal,
  IconPlus,
  IconRetry,
  loadBriefing,
  registry,
  RollingNumber,
  saveBriefingPaper,
  setSetting,
  subscribeNotifications,
  THEME_SETTING_KEY,
  THEMES,
  toastsStore,
  useBriefing,
  useGraphicsQuality,
  useSetting,
  useThemeId,
  type BriefingPaper,
  type BriefingSection,
  type BriefingStory,
  type GraphicsQuality,
} from '@horrible/core';

import { NAME_SETTING_KEY } from '../home/constants';
import { useAppFullscreen } from '../hooks/useAppFullscreen';
import './spotlight-hub.css';

/** Staggered entrance, capped so a long list never takes long to land. */
function enter(i: number): CSSProperties {
  return { '--sp-i': Math.min(i, 8) } as CSSProperties;
}

function runCommand(id: string): void {
  void registry.runCommand(id).catch((err: unknown) => {
    toastsStore.add('error', 'Command failed', String(err), 4000);
  });
}

function ago(epochMs: number, now = Date.now()): string {
  const m = Math.max(0, Math.floor((now - epochMs) / 60_000));
  if (m < 1) return 'now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function greeting(hour: number): string {
  if (hour < 5) return 'Good night';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

export function SpotlightHub() {
  const briefing = useBriefing();
  const name = useSetting<string>(NAME_SETTING_KEY)?.trim();
  const now = new Date();

  useEffect(() => {
    void loadBriefing();
  }, []);

  const refreshing = briefing.papers.status === 'loading' || briefing.news.status === 'loading';

  return (
    // Clicks inside the hub never reach the backdrop's close handler.
    <div className="sp-hub" onClick={(e) => e.stopPropagation()}>
      <div className="sp-hub-main">
        <header className="sp-hub-greeting" style={enter(0)}>
          <div>
            <p className="sp-meta">
              {now.toLocaleDateString(undefined, {
                weekday: 'long',
                month: 'long',
                day: 'numeric',
              })}
            </p>
            <h2>
              {greeting(now.getHours())}
              {name ? `, ${name}` : ''}
            </h2>
          </div>
          <button
            type="button"
            className="sp-icon-btn"
            onClick={() => runCommand('briefing.refresh')}
            disabled={refreshing}
            aria-label="Refresh the briefing"
            title="Refresh the briefing"
          >
            <IconRetry className={`hd-icon${refreshing ? ' sp-spin' : ''}`} />
          </button>
        </header>
        <PapersCard section={briefing.papers} />
        <HeadlinesCard section={briefing.news} />
      </div>
      <aside className="sp-hub-side">
        <QuickSettings />
        <NotificationsCard />
      </aside>
    </div>
  );
}

// --- shared --------------------------------------------------------------------

function SectionHead({
  title,
  meta,
  section,
}: {
  title: string;
  meta: string;
  section: BriefingSection<unknown>;
}) {
  return (
    <header className="sp-card-head">
      <h3 className="sp-h">{title}</h3>
      <span className="sp-meta">
        {section.stale || (section.status === 'error' && section.items.length > 0)
          ? `${meta} · cached ${section.fetchedAt ? ago(section.fetchedAt) : ''} ago`
          : meta}
      </span>
    </header>
  );
}

function SectionBody<T>({
  section,
  skeletonRows,
  children,
}: {
  section: BriefingSection<T>;
  skeletonRows: number;
  children: (items: T[]) => React.ReactNode;
}) {
  if (section.items.length > 0) return <>{children(section.items)}</>;
  if (section.status === 'error') {
    return (
      <div className="sp-empty">
        <p>Couldn’t reach the source.</p>
        <p className="sp-meta">{section.error}</p>
        <button
          type="button"
          className="sp-text-btn"
          onClick={() => runCommand('briefing.refresh')}
        >
          Try again
        </button>
      </div>
    );
  }
  if (section.status === 'ready') return <p className="sp-empty">Nothing yet this week.</p>;
  return (
    <ul className="sp-skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: skeletonRows }, (_, i) => (
        <li key={i} />
      ))}
    </ul>
  );
}

// --- papers --------------------------------------------------------------------

function PapersCard({ section }: { section: BriefingSection<BriefingPaper> }) {
  return (
    <section className="sp-card sp-papers" style={enter(1)} aria-label="Top papers this week">
      <SectionHead title="Top papers" meta="past 7 days · Hugging Face upvotes" section={section} />
      <SectionBody section={section} skeletonRows={5}>
        {([hero, ...rest]) => (
          <>
            <PaperHero paper={hero} />
            <ol className="sp-paper-list" start={2}>
              {rest.slice(0, 7).map((p, i) => (
                <li key={p.arxiv_id} className="sp-paper-row" style={enter(i + 2)}>
                  <span className="sp-rank">{String(i + 2).padStart(2, '0')}</span>
                  <div className="sp-paper-row-body">
                    <a
                      href={arxivAbsUrl(p.arxiv_id)}
                      target="_blank"
                      rel="noreferrer"
                      className="sp-title-link"
                    >
                      {p.title}
                    </a>
                    <span className="sp-meta">
                      {p.organization ?? p.authors.slice(0, 2).join(', ')}
                    </span>
                  </div>
                  <span className="sp-meta sp-votes" title={`${p.upvotes} upvotes`}>
                    <IconArrowUp />
                    <RollingNumber value={p.upvotes} />
                  </span>
                  <SaveButton paper={p} compact />
                </li>
              ))}
            </ol>
          </>
        )}
      </SectionBody>
    </section>
  );
}

function PaperHero({ paper }: { paper: BriefingPaper }) {
  const [thumbOk, setThumbOk] = useState(true);
  return (
    <article className="sp-hero" style={enter(1)}>
      {paper.thumbnail && thumbOk && (
        <a
          href={hfPaperUrl(paper.arxiv_id)}
          target="_blank"
          rel="noreferrer"
          className="sp-hero-thumb"
          tabIndex={-1}
        >
          <img src={paper.thumbnail} alt="" loading="lazy" onError={() => setThumbOk(false)} />
        </a>
      )}
      <div className="sp-hero-body">
        <p className="sp-meta">
          <span className="sp-rank">01</span> {paper.organization ? `· ${paper.organization}` : ''}
        </p>
        <a
          href={arxivAbsUrl(paper.arxiv_id)}
          target="_blank"
          rel="noreferrer"
          className="sp-hero-title"
        >
          {paper.title}
        </a>
        {paper.authors.length > 0 && <p className="sp-hero-authors">{paper.authors.join(', ')}</p>}
        {paper.summary && <p className="sp-hero-summary">{paper.summary}</p>}
        <div className="sp-hero-foot">
          <span className="sp-meta sp-hero-stats">
            <span title="Upvotes">
              <IconArrowUp />
              <RollingNumber value={paper.upvotes} />
            </span>
            <span title="Comments">
              <IconComment />
              <RollingNumber value={paper.comments} />
            </span>
            {paper.github_stars !== null && paper.github_url && (
              <a href={paper.github_url} target="_blank" rel="noreferrer" title="GitHub stars">
                <RollingNumber value={paper.github_stars} /> stars
              </a>
            )}
          </span>
          <span className="sp-hero-actions">
            <a
              href={arxivPdfUrl(paper.arxiv_id)}
              target="_blank"
              rel="noreferrer"
              className="sp-text-btn"
            >
              PDF <IconExternal />
            </a>
            <a
              href={hfPaperUrl(paper.arxiv_id)}
              target="_blank"
              rel="noreferrer"
              className="sp-text-btn"
            >
              Discuss <IconExternal />
            </a>
            <SaveButton paper={paper} />
          </span>
        </div>
      </div>
    </article>
  );
}

/** Files the paper into the library through the arXiv module's download route. */
function SaveButton({ paper, compact = false }: { paper: BriefingPaper; compact?: boolean }) {
  const [state, setState] = useState<'idle' | 'saving' | 'saved'>('idle');
  const save = () => {
    setState('saving');
    saveBriefingPaper(paper.arxiv_id).then(
      () => {
        setState('saved');
        toastsStore.add('success', 'Saved to the library', paper.title, 3000);
      },
      (err: unknown) => {
        setState('idle');
        toastsStore.add('error', "Couldn't save the paper", String(err), 5000);
      },
    );
  };
  const label = state === 'saved' ? 'Saved' : state === 'saving' ? 'Saving…' : 'Save';
  return (
    <button
      type="button"
      className={compact ? 'sp-icon-btn sp-save' : 'sp-text-btn sp-save'}
      onClick={save}
      disabled={state !== 'idle'}
      aria-label={`${label}: ${paper.title}`}
      title={compact ? 'Save to the library' : undefined}
    >
      {state === 'saved' ? <IconCheck /> : <IconPlus />}
      {!compact && label}
    </button>
  );
}

// --- headlines -------------------------------------------------------------------

function HeadlinesCard({ section }: { section: BriefingSection<BriefingStory> }) {
  return (
    <section className="sp-card" style={enter(2)} aria-label="AI headlines">
      <SectionHead title="Headlines" meta="past 48h · Hacker News" section={section} />
      <SectionBody section={section} skeletonRows={4}>
        {(stories) => (
          <ol className="sp-story-list">
            {stories.slice(0, 8).map((s, i) => (
              <li key={s.id} className="sp-story" style={enter(i + 3)}>
                <span className="sp-rank">{String(i + 1).padStart(2, '0')}</span>
                <div className="sp-story-body">
                  <a href={s.url} target="_blank" rel="noreferrer" className="sp-title-link">
                    {s.title}
                  </a>
                  <span className="sp-meta">
                    {s.domain} · <RollingNumber value={s.points} /> pts ·{' '}
                    <a
                      href={s.discussion_url}
                      target="_blank"
                      rel="noreferrer"
                      className="sp-meta-link"
                    >
                      <IconComment /> {s.comments}
                    </a>{' '}
                    · {ago(s.created_at * 1000)}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        )}
      </SectionBody>
    </section>
  );
}

// --- quick settings --------------------------------------------------------------

const QUALITY_LABELS: Record<GraphicsQuality, string> = {
  performance: 'Fast',
  balanced: 'Balanced',
  quality: 'Quality',
};

function QuickSettings() {
  const themeId = useThemeId();
  const quality = useGraphicsQuality();
  const desktopNotes = useSetting<boolean>('notifications.desktop') !== false;
  const canFullscreen = hasCapability('window.fullscreen');
  const { fullscreen, toggle } = useAppFullscreen();

  return (
    <section className="sp-card" style={enter(1)} aria-label="Quick settings">
      <header className="sp-card-head">
        <h3 className="sp-h">Quick settings</h3>
        <button type="button" className="sp-text-btn" onClick={() => runCommand('settings.open')}>
          All settings
        </button>
      </header>

      <p className="sp-label">Theme</p>
      <div className="sp-chips" role="radiogroup" aria-label="Theme">
        {THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={t.id === themeId}
            className="sp-chip"
            title={t.description}
            onClick={() => void setSetting(THEME_SETTING_KEY, t.id)}
          >
            {t.title}
          </button>
        ))}
      </div>

      <p className="sp-label">Graphics</p>
      <div className="sp-seg" role="radiogroup" aria-label="Graphics quality">
        {(Object.keys(QUALITY_LABELS) as GraphicsQuality[]).map((q) => (
          <button
            key={q}
            type="button"
            role="radio"
            aria-checked={q === quality}
            className="sp-seg-btn"
            onClick={() => void setSetting(GRAPHICS_QUALITY_SETTING_KEY, q)}
          >
            {QUALITY_LABELS[q]}
          </button>
        ))}
      </div>

      <div className="sp-tiles">
        <button
          type="button"
          role="switch"
          aria-checked={desktopNotes}
          className="sp-tile"
          onClick={() => void setSetting('notifications.desktop', !desktopNotes)}
        >
          <span className="sp-tile-label">Desktop alerts</span>
          <span className="sp-meta">{desktopNotes ? 'on' : 'off'}</span>
        </button>
        {canFullscreen && (
          <button
            type="button"
            role="switch"
            aria-checked={fullscreen}
            className="sp-tile"
            onClick={toggle}
          >
            <span className="sp-tile-label">Fullscreen</span>
            <span className="sp-meta">{fullscreen ? 'on' : 'off'}</span>
          </button>
        )}
      </div>
    </section>
  );
}

// --- notifications ---------------------------------------------------------------

function NotificationsCard() {
  const items = useSyncExternalStore(subscribeNotifications, getNotifications, getNotifications);
  const unread = items.filter((n) => !n.read).length;
  return (
    <section className="sp-card" style={enter(2)} aria-label="Notifications">
      <header className="sp-card-head">
        <h3 className="sp-h">Notifications</h3>
        <span className="sp-meta">{unread > 0 ? `${unread} unread` : 'all read'}</span>
      </header>
      {items.length === 0 ? (
        <p className="sp-empty">Nothing new.</p>
      ) : (
        <ul className="sp-notes">
          {/* A glance, not the inbox: the clock flyout marks read and holds the tail. */}
          {items.slice(0, 5).map((n, i) => (
            <li
              key={n.id}
              className={`sp-note is-${n.kind}${n.read ? '' : ' is-unread'}`}
              style={enter(i + 3)}
            >
              <IconDot className="hd-icon sp-note-dot" />
              <span className="sp-note-body">
                <strong>{n.title}</strong>
                {n.body && <span>{n.body}</span>}
                <span className="sp-meta">{ago(n.at)} ago</span>
              </span>
              <button
                type="button"
                className="sp-icon-btn"
                aria-label={`Dismiss ${n.title}`}
                onClick={() => dismissNotification(n.id)}
              >
                <IconClose />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
