/**
 * Publish a site: pick its theme, point it at GitHub Pages, see which pages go and
 * which stay home, build a local preview, and publish.
 *
 * Publishing is this pane's buttons and nothing else — no agent tool can reach it.
 * In **static** mode the site is built here, in the browser, by the same renderer as
 * the Preview (`site/build.tsx`), and the backend completes it (media, scenes, share
 * cards) and pushes it as one commit. In **jupyter-book** mode the backend pushes the
 * sources and a workflow that builds them on GitHub.
 *
 * Preflight runs on the exact files about to leave. Something secret-shaped stops
 * the publish until the person chooses "Publish anyway"; warnings are listed and do
 * not stop it.
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';

import { openExternal, openPath } from '../../../external';
import { usePaneParams } from '../../../panes';
import { subscribeChannel } from '../../../ws';
import {
  buildSiteLocally,
  builtUrl,
  getPublishState,
  getSite,
  listPages,
  listThemes,
  publishSite,
  SCRIVE_CHANNEL,
  updateSiteConfig,
  type BuildOutcome,
  type PageMeta,
  type PagesConfig,
  type PublishFinding,
  type PublishOutcome,
  type PublishState,
  type SiteBundle,
  type SiteConfig,
  type ThemeInfo,
} from '../api';
import { CheckIcon, ExternalIcon, PublishIcon, RefreshIcon } from '../icons';
import { openScrivePage } from '../open';
import { setPageField } from '../posts';
import { isPublic } from '../site/build';
import { themePalettes } from '../site/palette';
import { buildBundle } from '../site/prepare';
import { useCurrentSite } from '../state';
import '../scrive.css';

const STAGGER_MS = 24;
const STAGGER_CAP = 10;

type Phase =
  | { kind: 'idle' }
  | { kind: 'working'; label: string }
  | { kind: 'built'; outcome: BuildOutcome }
  | { kind: 'blocked'; outcome: PublishOutcome; bundle: SiteBundle | null }
  | { kind: 'published'; outcome: PublishOutcome };

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function when(seconds: number): string {
  const d = new Date(seconds * 1000);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

/** An absolute URL for something `apiUrl` may have left relative. */
function absolute(url: string): string {
  return new URL(url, window.location.href).href;
}

export function PublishPanel() {
  const params = usePaneParams();
  const current = useCurrentSite();
  const site = String(params.site ?? '') || current || '';
  if (!site) {
    return (
      <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>
        Pick a site in the Scrive pane first.
      </div>
    );
  }
  return <Publisher key={site} site={site} />;
}

function Publisher({ site }: { site: string }) {
  const [state, setState] = useState<PublishState | null>(null);
  const [config, setConfig] = useState<SiteConfig | null>(null);
  const [siteTitle, setSiteTitle] = useState(site);
  const [themes, setThemes] = useState<ThemeInfo[]>([]);
  const [pages, setPages] = useState<PageMeta[]>([]);
  const [form, setForm] = useState<PagesConfig>({ repo: '', mode: 'static', cname: '' });
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([getPublishState(site), getSite(site), listThemes(site), listPages(site)]).then(
      ([publishState, detail, themeList, pageList]) => {
        setState(publishState);
        setForm(publishState.config);
        setConfig(detail.config);
        setSiteTitle(detail.config.title || detail.site.title);
        setThemes(themeList);
        setPages(pageList);
      },
      (e: unknown) => setError(message(e)),
    );
  }, [site]);

  useEffect(load, [load]);
  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        const data = msg.data as { site?: string };
        if (data?.site !== site) return;
        if (msg.event === 'page.changed') listPages(site).then(setPages, () => {});
        if (msg.event === 'site.published') getPublishState(site).then(setState, () => {});
      }),
    [site],
  );

  const going = useMemo(() => pages.filter(isPublic), [pages]);
  const held = useMemo(() => pages.filter((p) => !isPublic(p)), [pages]);
  const busy = phase.kind === 'working';

  const saveTarget = async (next: PagesConfig) => {
    setForm(next);
    try {
      const saved = await updateSiteConfig(site, { pages: next });
      setConfig(saved);
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  };

  const chooseTheme = async (theme: string) => {
    try {
      setConfig(await updateSiteConfig(site, { theme }));
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  };

  const include = async (page: PageMeta) => {
    try {
      // A post goes out once published; a page held back by its status is released.
      await setPageField(site, page.path, 'status', page.kind === 'post' ? 'published' : null);
      setPages(await listPages(site));
    } catch (e) {
      setError(message(e));
    }
  };

  const build = async () => {
    setError(null);
    setPhase({ kind: 'working', label: 'Rendering pages' });
    try {
      const { bundle } = await buildBundle(site);
      setPhase({ kind: 'working', label: 'Writing the preview' });
      const outcome = await buildSiteLocally(site, bundle);
      setPhase({ kind: 'built', outcome });
      getPublishState(site).then(setState, () => {});
    } catch (e) {
      setPhase({ kind: 'idle' });
      setError(message(e));
    }
  };

  const publish = async (acknowledged = false, ready: SiteBundle | null = null) => {
    setError(null);
    try {
      let bundle = ready;
      if (form.mode === 'static' && !bundle) {
        setPhase({ kind: 'working', label: 'Rendering pages' });
        bundle = (await buildBundle(site)).bundle;
      }
      setPhase({ kind: 'working', label: 'Pushing to GitHub' });
      const outcome = await publishSite(site, { bundle: bundle ?? undefined, acknowledged });
      if (!outcome.published) setPhase({ kind: 'blocked', outcome, bundle });
      else {
        setPhase({ kind: 'published', outcome });
        getPublishState(site).then(setState, () => {});
      }
    } catch (e) {
      setPhase({ kind: 'idle' });
      setError(message(e));
    }
  };

  if (!state || !config) {
    return (
      <div
        style={{ padding: 'var(--space-4)', color: error ? 'var(--danger)' : 'var(--text-dim)' }}
      >
        {error ?? 'Loading…'}
      </div>
    );
  }

  const record = state.record;
  const theme = themes.find((t) => t.id === config.theme);
  const repoLabel = form.repo.trim() || `${site} (a public repository on your account)`;

  return (
    <div className="scrive-publish">
      <header className="scrive-outline-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head">Publish · {siteTitle}</div>
          <div
            className="scrive-meta"
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {theme?.name ?? config.theme} ·{' '}
            {form.mode === 'static' ? 'static site' : 'jupyter book'} · {going.length} page
            {going.length === 1 ? '' : 's'} ready
          </div>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          {form.mode === 'static' && (
            <button
              type="button"
              onClick={() => void build()}
              disabled={busy}
              title="Build the site into _build/site/ and preview it, without publishing"
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <RefreshIcon /> Build preview
              </span>
            </button>
          )}
          <button
            type="button"
            style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
            onClick={() => void publish()}
            disabled={busy || !state.available || going.length === 0}
            title={
              state.available
                ? `Publish ${going.length} pages to GitHub Pages (public)`
                : state.reason
            }
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <PublishIcon /> Publish site
            </span>
          </button>
        </div>
      </header>

      {error && (
        <div className="scrive-publish-error" role="alert">
          {error}
        </div>
      )}

      <div className="scrive-publish-body">
        <div className="scrive-publish-main">
          <section className="scrive-publish-section">
            <div className="scrive-publish-section-head">
              <span className="scrive-head">Theme</span>
              <span className="scrive-meta">how the published site looks</span>
            </div>
            <div className="scrive-theme-grid">
              {themes.map((t, i) => (
                <ThemeCard
                  key={t.id}
                  theme={t}
                  selected={t.id === config.theme}
                  index={i}
                  onSelect={() => void chooseTheme(t.id)}
                />
              ))}
            </div>
          </section>

          <section className="scrive-publish-section">
            <div className="scrive-publish-section-head">
              <span className="scrive-head">Pages</span>
              <span className="scrive-meta">
                {going.length} going · {held.length} staying home
              </span>
            </div>
            <ul className="scrive-publish-pages">
              {going.map((p, i) => (
                <PageRow key={p.path} page={p} index={i} site={site} />
              ))}
              {held.map((p, i) => (
                <PageRow
                  key={p.path}
                  page={p}
                  index={going.length + i}
                  site={site}
                  onInclude={() => void include(p)}
                />
              ))}
            </ul>
            {held.some((p) => p.kind === 'post') && (
              <p className="scrive-meta scrive-publish-note">
                A post goes out once its status is published — on the posts board, or with "mark
                published" here.
              </p>
            )}
          </section>
        </div>

        <aside className="scrive-publish-side">
          <section className="scrive-publish-section">
            <div className="scrive-publish-section-head">
              <span className="scrive-head">GitHub Pages</span>
            </div>
            {!state.available && <p className="scrive-publish-warn">{state.reason}</p>}
            <div className="scrive-seg" role="group" aria-label="Build">
              {(
                [
                  ['static', 'Static site'],
                  ['jupyter-book', 'Jupyter Book'],
                ] as const
              ).map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  className="scrive-seg-btn"
                  aria-pressed={form.mode === mode}
                  onClick={() => void saveTarget({ ...form, mode })}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="scrive-meta scrive-publish-note">
              {form.mode === 'static'
                ? 'Built here with the theme above, pushed as one commit.'
                : 'Sources pushed with a workflow that runs jupyter book build on GitHub; the theme above does not apply.'}
            </p>
            <label className="scrive-outline-field">
              <span className="scrive-head">Repository</span>
              <input
                type="text"
                value={form.repo}
                placeholder={`${site}  or  owner/repo`}
                onChange={(e) => setForm({ ...form, repo: e.target.value })}
                onBlur={() => void saveTarget(form)}
                style={{ padding: '0 0.6rem' }}
              />
            </label>
            <label className="scrive-outline-field">
              <span className="scrive-head">Custom domain</span>
              <input
                type="text"
                value={form.cname}
                placeholder="blog.example.com (optional)"
                onChange={(e) => setForm({ ...form, cname: e.target.value })}
                onBlur={() => void saveTarget(form)}
                style={{ padding: '0 0.6rem' }}
              />
            </label>
            <p className="scrive-meta scrive-publish-note">
              Publishes to {repoLabel}. Pages sites are public.
            </p>
          </section>

          <section className="scrive-publish-section">
            <div className="scrive-publish-section-head">
              <span className="scrive-head">Status</span>
            </div>
            {record ? (
              <div className="scrive-publish-record">
                <button
                  type="button"
                  className="scrive-publish-link"
                  onClick={() => void openExternal(record.url)}
                >
                  {record.url} <ExternalIcon size={12} />
                </button>
                <div className="scrive-meta">
                  {when(record.published_at)} · {record.pages} pages ·{' '}
                  {record.commit ? record.commit.slice(0, 7) : 'no change'}
                </div>
              </div>
            ) : (
              <p className="scrive-meta">Not published yet.</p>
            )}
            <PhaseView
              phase={phase}
              site={site}
              onAcknowledge={(bundle) => void publish(true, bundle)}
            />
          </section>
        </aside>
      </div>
    </div>
  );
}

function ThemeCard({
  theme,
  selected,
  index,
  onSelect,
}: {
  theme: ThemeInfo;
  selected: boolean;
  index: number;
  onSelect: () => void;
}) {
  const { base, alt } = useMemo(() => themePalettes(theme.tokens), [theme.tokens]);
  const swatches = (palette: Record<string, string>) =>
    ['bg', 'bg-raised', 'text', 'accent'].map((key) => (
      <span
        key={key}
        className="scrive-theme-swatch"
        style={{ background: palette[key] ?? 'transparent' }}
        title={`--${key}`}
      />
    ));
  return (
    <button
      type="button"
      className="scrive-theme-card"
      aria-pressed={selected}
      onClick={onSelect}
      style={{ animationDelay: `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms` }}
    >
      <span className="scrive-theme-swatches">
        {swatches(base)}
        {alt && <span className="scrive-theme-swatch-gap" />}
        {alt && swatches(alt.palette)}
      </span>
      <span className="scrive-theme-name">
        {selected && <CheckIcon size={12} />}
        {theme.name}
        {theme.source === 'site' && <span className="scrive-chip">site</span>}
      </span>
      <span className="scrive-theme-desc">{theme.description}</span>
    </button>
  );
}

function PageRow({
  page,
  index,
  site,
  onInclude,
}: {
  page: PageMeta;
  index: number;
  site: string;
  onInclude?: () => void;
}) {
  return (
    <li
      className="scrive-publish-page"
      data-held={onInclude ? 'true' : undefined}
      style={{ '--i': Math.min(index, STAGGER_CAP) } as CSSProperties}
    >
      <button
        type="button"
        className="scrive-row"
        onClick={() => openScrivePage(site, page.path)}
        title={page.path}
      >
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {page.title}
        </span>
        <span className="scrive-meta">{page.date || page.kind}</span>
        <span className="scrive-chip" data-status={page.status || undefined}>
          {page.status || (onInclude ? 'draft' : 'page')}
        </span>
      </button>
      {onInclude && (
        <button
          type="button"
          className="scrive-publish-include"
          onClick={onInclude}
          title="Set this page's status to published, so the next publish includes it"
        >
          mark published
        </button>
      )}
    </li>
  );
}

function PhaseView({
  phase,
  site,
  onAcknowledge,
}: {
  phase: Phase;
  site: string;
  onAcknowledge: (bundle: SiteBundle | null) => void;
}) {
  switch (phase.kind) {
    case 'idle':
      return null;
    case 'working':
      return (
        <div className="scrive-publish-phase" role="status" aria-busy="true">
          <span className="scrive-publish-pulse" /> {phase.label}…
        </div>
      );
    case 'built':
      return (
        <div className="scrive-publish-phase" role="status">
          <div>
            Built {phase.outcome.files} files.{' '}
            <button
              type="button"
              className="scrive-publish-link"
              onClick={() => void openExternal(absolute(builtUrl(site)))}
            >
              Open preview <ExternalIcon size={12} />
            </button>{' '}
            <button
              type="button"
              className="scrive-publish-link"
              onClick={() => void openPath(phase.outcome.path)}
              title={phase.outcome.path}
            >
              Show folder
            </button>
          </div>
          {phase.outcome.note && <p className="scrive-meta">{phase.outcome.note}</p>}
          <Findings findings={phase.outcome.findings} />
        </div>
      );
    case 'blocked': {
      const { findings } = phase.outcome;
      const empty = findings.some((f) => f.rule === 'empty');
      return (
        <div className="scrive-publish-phase" role="alert">
          <div className="scrive-publish-warn">
            Not published:{' '}
            {empty ? 'there is nothing ready to publish.' : 'preflight found something to check.'}
          </div>
          <Findings findings={findings} />
          {!empty && (
            <button
              type="button"
              style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }}
              onClick={() => onAcknowledge(phase.bundle)}
              title="These files will be public. Publish only if the findings are not real secrets."
            >
              Publish anyway
            </button>
          )}
        </div>
      );
    }
    case 'published': {
      const { outcome } = phase;
      return (
        <div className="scrive-publish-phase is-done" role="status">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <CheckIcon size={12} /> Published.
          </div>
          {outcome.note && <p className="scrive-meta">{outcome.note}</p>}
          <Findings findings={outcome.findings} />
        </div>
      );
    }
  }
}

function Findings({ findings }: { findings: PublishFinding[] }) {
  if (!findings.length) return null;
  return (
    <ul className="scrive-publish-findings">
      {findings.map((f, i) => (
        <li key={i} data-severity={f.blocking ? 'block' : f.severity}>
          <span className="scrive-meta">{f.file || f.rule}</span>
          <span>{f.message}</span>
        </li>
      ))}
    </ul>
  );
}
