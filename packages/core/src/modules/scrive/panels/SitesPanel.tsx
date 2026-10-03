/**
 * The Scrive catalog: pick a site, see its posts (newest first, by their own
 * frontmatter date), pages and notebooks, and start a new post.
 *
 * Everything listed is read from the files — the frontmatter is the property
 * schema, so a post edited in another editor shows its new status here on the next
 * `page.changed`. The table and board views over those properties are the Posts
 * pane (`PostsPanel`), opened from the header here.
 *
 * A new post starts blank or from a template (its inputs asked for inline). The
 * header's spark opens **Generate**: a request the agent turns into an outline for
 * review — it proposes, a person approves, and only then is anything written.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { openPane } from '../../../layout/controller';

import { subscribeChannel } from '../../../ws';
import { sendInChat } from '../../agent/openSession';
import {
  createPage,
  createSite,
  listPages,
  listSites,
  listTemplates,
  SCRIVE_CHANNEL,
  type PageChanged,
  type PageKind,
  type PageMeta,
  type SiteMeta,
  type TemplateInfo,
} from '../api';
import {
  BoardIcon,
  NotebookIcon,
  PageIcon,
  PlusIcon,
  PostIcon,
  FilmIcon,
  PublishIcon,
  RefreshIcon,
  SparkIcon,
} from '../icons';
import { openClip, openPublish, openScrivePage } from '../open';
import { generatePrompt } from '../prompts';
import { claimGenerate, onGenerate, setCurrentSite, useCurrentSite } from '../state';
import '../scrive.css';

const GROUPS: { kind: PageKind; label: string }[] = [
  { kind: 'post', label: 'Posts' },
  { kind: 'page', label: 'Pages' },
  { kind: 'notebook', label: 'Notebooks' },
];

/** Cap on the entrance stagger, so a site with 200 posts doesn't take seconds. */
const STAGGER_MS = 18;
const STAGGER_CAP = 12;

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function SitesPanel() {
  const [sites, setSites] = useState<SiteMeta[] | null>(null);
  const [pages, setPages] = useState<PageMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [generating, setGenerating] = useState(false);
  const site = useCurrentSite();

  useEffect(() => {
    if (!site) return;
    listTemplates(site).then(setTemplates, () => setTemplates([]));
  }, [site]);

  // The `scrive.generate` command: open the form here.
  useEffect(() => {
    if (claimGenerate()) setGenerating(true);
    return onGenerate(() => {
      claimGenerate();
      setGenerating(true);
    });
  }, []);

  const refreshSites = useCallback(async () => {
    try {
      const list = await listSites();
      setSites(list);
      setError(null);
      if (list.length && !list.some((s) => s.id === site)) setCurrentSite(list[0].id);
    } catch (e) {
      setError(message(e));
    }
  }, [site]);

  const refreshPages = useCallback(async () => {
    if (!site) {
      setPages([]);
      return;
    }
    try {
      setPages(await listPages(site));
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  }, [site]);

  useEffect(() => {
    void refreshSites();
  }, [refreshSites]);

  useEffect(() => {
    void refreshPages();
  }, [refreshPages]);

  // Re-list after on-disk changes to the current site, coalesced: a `git pull` is a
  // burst of events, and one listing after it settles is all that's needed.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'page.changed' || (msg.data as PageChanged).site !== site) return;
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => void refreshPages(), 250);
      }),
    [site, refreshPages],
  );

  const grouped = useMemo(
    () => GROUPS.map((g) => ({ ...g, items: pages.filter((p) => p.kind === g.kind) })),
    [pages],
  );

  if (sites === null) {
    return <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>Loading…</div>;
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
      <div className="scrive-bar" style={{ padding: 'var(--space-2) var(--space-3)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          <span className="scrive-head" style={{ flex: 1 }}>
            Scrive
          </span>
          <button
            type="button"
            className="btn-mini"
            title="Generate a page: the agent proposes an outline for you to approve"
            aria-label="Generate a page with the agent"
            aria-pressed={generating}
            disabled={!site}
            onClick={() => setGenerating((g) => !g)}
          >
            <SparkIcon size={12} />
          </button>
          <button
            type="button"
            className="btn-mini"
            title="Posts as a table and a board"
            aria-label="Open posts table and board"
            disabled={!site}
            onClick={() => void openPane('scrive.posts')}
          >
            <BoardIcon size={12} />
          </button>
          <button
            type="button"
            className="btn-mini"
            title="Theme and publishing: build the site, publish it to GitHub Pages"
            aria-label="Publish site"
            disabled={!site}
            onClick={() => site && openPublish(site)}
          >
            <PublishIcon size={12} />
          </button>
          <button
            type="button"
            className="btn-mini"
            title="Clips: cut, crop and caption a video, make a GIF, record the screen"
            aria-label="Open clips"
            disabled={!site}
            onClick={() => site && openClip(site)}
          >
            <FilmIcon size={12} />
          </button>
          <button
            type="button"
            className="btn-mini"
            title="Refresh"
            aria-label="Refresh"
            onClick={() => void refreshSites().then(refreshPages)}
          >
            <RefreshIcon size={12} />
          </button>
        </div>
        {sites.length > 0 && (
          <select
            aria-label="Site"
            value={site ?? ''}
            onChange={(e) => setCurrentSite(e.target.value)}
            style={{ width: '100%', marginTop: 'var(--space-2)', padding: '0 0.6rem' }}
          >
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} ({s.pages})
              </option>
            ))}
          </select>
        )}
      </div>

      {error && (
        <div style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}

      {sites.length === 0 ? (
        <NewSite
          onCreated={async (created) => {
            setCurrentSite(created.id);
            await refreshSites();
          }}
        />
      ) : (
        site && (
          <>
            {generating && (
              <Generate site={site} templates={templates} onSent={() => setGenerating(false)} />
            )}
            <NewPost
              site={site}
              templates={templates}
              onCreated={async (path) => {
                await refreshPages();
                openScrivePage(site, path);
              }}
            />
            <div
              style={{ flex: 1, minHeight: 0, overflow: 'auto', paddingBottom: 'var(--space-3)' }}
            >
              {grouped.map(
                (group) =>
                  group.items.length > 0 && (
                    <section key={group.kind} style={{ marginTop: 'var(--space-3)' }}>
                      <div
                        className="scrive-head"
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          padding: '0 var(--space-3) var(--space-1)',
                        }}
                      >
                        <span>{group.label}</span>
                        <span className="scrive-meta">{group.items.length}</span>
                      </div>
                      {group.items.map((page, i) => (
                        <PageRow
                          key={page.path}
                          page={page}
                          delay={Math.min(i, STAGGER_CAP) * STAGGER_MS}
                          onOpen={() => openScrivePage(site, page.path)}
                        />
                      ))}
                    </section>
                  ),
              )}
            </div>
          </>
        )
      )}
    </div>
  );
}

function PageRow({ page, delay, onOpen }: { page: PageMeta; delay: number; onOpen: () => void }) {
  const Icon = page.kind === 'post' ? PostIcon : page.kind === 'notebook' ? NotebookIcon : PageIcon;
  return (
    <button
      type="button"
      className="scrive-row"
      style={{ animationDelay: `${delay}ms` }}
      onClick={onOpen}
      title={page.path}
    >
      <span style={{ color: 'var(--text-dim)', display: 'inline-flex' }}>
        <Icon />
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {page.title}
        </span>
        {page.kind === 'post' && page.date && <span className="scrive-meta">{page.date}</span>}
      </span>
      {page.status && (
        <span className="scrive-chip" data-status={page.status}>
          {page.status}
        </span>
      )}
    </button>
  );
}

function TemplateSelect({
  templates,
  value,
  onChange,
  blank,
}: {
  templates: TemplateInfo[];
  value: string;
  onChange: (id: string) => void;
  blank: string;
}) {
  return (
    <select
      aria-label="Template"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ minWidth: 0, padding: '0 0.6rem' }}
    >
      <option value="">{blank}</option>
      {templates.map((t) => (
        <option key={t.id} value={t.id} title={t.description}>
          {t.name}
          {t.source === 'site' ? ' (site)' : ''}
        </option>
      ))}
    </select>
  );
}

function NewPost({
  site,
  templates,
  onCreated,
}: {
  site: string;
  templates: TemplateInfo[];
  onCreated: (path: string) => Promise<void>;
}) {
  const [title, setTitle] = useState('');
  const [template, setTemplate] = useState('');
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const chosen = templates.find((t) => t.id === template);
  const missing = Object.entries(chosen?.inputs ?? {}).some(
    ([key, spec]) => spec.required && !inputs[key]?.trim(),
  );

  const submit = async () => {
    const t = title.trim();
    if (!t || missing) return;
    try {
      const page = await createPage(site, {
        kind: chosen?.kind ?? 'post',
        title: t,
        ...(chosen ? { template: chosen.id, inputs } : {}),
      });
      setTitle('');
      setInputs({});
      setError(null);
      await onCreated(page.meta.path);
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
      style={{ display: 'grid', gap: 'var(--space-2)', padding: 'var(--space-2) var(--space-3)' }}
    >
      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
        <input
          type="text"
          aria-label="New post title"
          placeholder="New post title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          style={{ flex: 1, minWidth: 0, padding: '0 0.6rem' }}
        />
        <button type="submit" disabled={!title.trim() || missing} title="Create post">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <PlusIcon /> Post
          </span>
        </button>
      </div>
      {templates.length > 0 && (
        <TemplateSelect
          templates={templates}
          value={template}
          onChange={(id) => {
            setTemplate(id);
            setInputs({});
          }}
          blank="Blank post"
        />
      )}
      {chosen &&
        Object.entries(chosen.inputs).map(([key, spec]) => (
          <input
            key={key}
            type="text"
            aria-label={key}
            placeholder={`${key}${spec.required ? ' *' : ''} — ${spec.description}`}
            title={spec.description}
            value={inputs[key] ?? ''}
            onChange={(e) => setInputs((v) => ({ ...v, [key]: e.target.value }))}
            style={{ minWidth: 0, padding: '0 0.6rem' }}
          />
        ))}
      {error && <span style={{ color: 'var(--danger)' }}>{error}</span>}
    </form>
  );
}

/** "Generate a page": the request goes to the agent, which proposes an outline. */
function Generate({
  site,
  templates,
  onSent,
}: {
  site: string;
  templates: TemplateInfo[];
  onSent: () => void;
}) {
  const [request, setRequest] = useState('');
  const [kind, setKind] = useState<'post' | 'page'>('post');
  const [template, setTemplate] = useState('');

  return (
    <form
      className="scrive-generate"
      onSubmit={(e) => {
        e.preventDefault();
        if (!request.trim()) return;
        sendInChat(generatePrompt({ site, request, kind, template: template || undefined }));
        setRequest('');
        onSent();
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-2)' }}>
        <span className="scrive-head" style={{ flex: 1 }}>
          Generate
        </span>
        <span className="scrive-meta">outline first, then you approve</span>
      </div>
      <textarea
        aria-label="What the page should be"
        placeholder="What should it be about? Who is it for? Anything it must include?"
        rows={3}
        value={request}
        autoFocus
        onChange={(e) => setRequest(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) e.currentTarget.form?.requestSubmit();
        }}
        style={{ width: '100%', resize: 'vertical', font: 'inherit' }}
      />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto minmax(0, 1fr)',
          gap: 'var(--space-2)',
        }}
      >
        <select
          aria-label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as 'post' | 'page')}
          style={{ padding: '0 0.6rem' }}
        >
          <option value="post">post</option>
          <option value="page">page</option>
        </select>
        <TemplateSelect
          templates={templates}
          value={template}
          onChange={setTemplate}
          blank="Agent picks the shape"
        />
      </div>
      <button
        type="submit"
        disabled={!request.trim()}
        title="Ask the agent for an outline (Ctrl+Enter)"
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <SparkIcon /> Ask for an outline
        </span>
      </button>
    </form>
  );
}

function NewSite({ onCreated }: { onCreated: (site: SiteMeta) => Promise<void> }) {
  const [title, setTitle] = useState('');
  const [error, setError] = useState<string | null>(null);
  const id = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);

  const submit = async () => {
    if (!id) return;
    try {
      await onCreated(await createSite(id, title.trim()));
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
      style={{ display: 'grid', gap: 'var(--space-2)', padding: 'var(--space-4) var(--space-3)' }}
    >
      <div className="scrive-head">New site</div>
      <p style={{ margin: 0, color: 'var(--text-dim)' }}>
        A site is a folder of MyST pages that Jupyter Book can build and git can track.
      </p>
      <input
        type="text"
        aria-label="Site title"
        placeholder="Site title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        style={{ padding: '0 0.6rem' }}
      />
      {id && <span className="scrive-meta">folder: {id}/</span>}
      <button type="submit" disabled={!id}>
        Create site
      </button>
      {error && <span style={{ color: 'var(--danger)' }}>{error}</span>}
    </form>
  );
}
