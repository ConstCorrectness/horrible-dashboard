/**
 * One item's card: identity, every metric (unknowns as "—"), what you can do with it,
 * the facts upstream gave, and the README / abstract / SKILL.md.
 *
 * Actions reuse the flows that already own them — the arXiv module's save-to-library,
 * the doc viewer's capture, the MCP pane's install (secrets and all) — rather than
 * re-implementing them here.
 */
import { useMemo, useState } from 'react';

import { IconCheck, IconExternal, IconPin, IconPlus } from '../../../glyphs';
import { renderMarkdown } from '../../../notebook/markdown';
import { registry } from '../../../registry';
import { getSetting, setSetting, useSetting } from '../../../settings';
import { toastsStore } from '../../../toasts';
import { captureDocSet, installSkill, saveArxivPaper, type DiscoverItem } from '../api';
import { cleanReadme, day } from '../format';
import { refresh, setActiveKind, setFilter, type DetailState } from '../store';
import { MetricCell } from './MetricCell';

/** Cap on rendered README length: a 100 KB model card rendered whole is a long
 *  freeze for a glance. The full card is one click away upstream. */
const BODY_CHARS = 40_000;

export function DetailView({
  source,
  kind,
  item,
  detail,
}: {
  source: string;
  kind: string;
  item: DiscoverItem;
  detail: DetailState | undefined;
}) {
  // The list row renders instantly; the fetched card replaces it when it lands.
  const shown = detail?.data?.item ?? item;
  const facts = detail?.data?.facts ?? item.facts;
  const links = detail?.data?.links ?? [];
  const files = detail?.data?.files ?? [];
  const [showFiles, setShowFiles] = useState(false);

  return (
    <article className="dc-card">
      <header className="dc-card-head">
        <div className="dc-card-titles">
          <h3 className="dc-h dc-card-title">{shown.title}</h3>
          {shown.subtitle && <p className="dc-meta">{shown.subtitle}</p>}
          <p className="dc-meta">
            {[
              shown.author && `by ${shown.author}`,
              day(shown.created_at) && `created ${day(shown.created_at)}`,
              day(shown.updated_at) && `updated ${day(shown.updated_at)}`,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
        </div>
        {shown.thumbnail && <Thumb src={shown.thumbnail} />}
      </header>

      {shown.badges.length > 0 && (
        <div className="dc-badges">
          {shown.badges.map((b) => (
            <span
              key={b.label}
              className="dc-badge"
              data-tone={b.tone}
              title={b.title ?? undefined}
            >
              {b.label}
            </span>
          ))}
        </div>
      )}

      {shown.metrics.length > 0 && (
        <dl className="dc-metrics">
          {shown.metrics.map((m) => (
            <div key={m.key} className="dc-metric">
              <dt>{m.label}</dt>
              <dd>
                <MetricCell metric={{ ...m, label: '' }} />
              </dd>
            </div>
          ))}
        </dl>
      )}

      <div className="dc-actions">
        {shown.url && (
          <a className="dc-action" href={shown.url} target="_blank" rel="noreferrer noopener">
            Open <IconExternal />
          </a>
        )}
        <SourceActions source={source} kind={kind} item={shown} />
      </div>

      {detail?.status === 'loading' && <p className="dc-meta">Loading the full card…</p>}
      {detail?.status === 'error' && <p className="dc-detail-error">{detail.error}</p>}

      {facts.length > 0 && (
        <dl className="dc-facts">
          {facts.map((f) => (
            <div key={`${f.label}:${f.value}`}>
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {links.length > 0 && (
        <div className="dc-links">
          {links.map((l) => (
            <a key={l.url} href={l.url} target="_blank" rel="noreferrer noopener">
              {l.label} <IconExternal />
            </a>
          ))}
        </div>
      )}

      {shown.tags.length > 0 && (
        <p className="dc-tags">
          {shown.tags.map((t) => (
            <span key={t}>{t}</span>
          ))}
        </p>
      )}

      {files.length > 0 && (
        <div className="dc-files">
          <button type="button" className="dc-text-btn" onClick={() => setShowFiles((v) => !v)}>
            {showFiles ? 'Hide' : 'Show'} {files.length} files
          </button>
          {showFiles && (
            <ul>
              {files.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <Body body={detail?.data?.body ?? null} format={detail?.data?.body_format ?? 'text'} />
    </article>
  );
}

function Thumb({ src }: { src: string }) {
  const [ok, setOk] = useState(true);
  if (!ok) return null;
  return <img className="dc-thumb" src={src} alt="" loading="lazy" onError={() => setOk(false)} />;
}

function Body({ body, format }: { body: string | null; format: 'markdown' | 'text' }) {
  const html = useMemo(
    () => (body && format === 'markdown' ? renderMarkdown(cleanReadme(body.slice(0, BODY_CHARS))) : null),
    [body, format],
  );
  if (!body) return null;
  const truncated = body.length > BODY_CHARS;
  return (
    <section className="dc-body-text" aria-label="Description">
      {html !== null ? (
        // `renderMarkdown` escapes HTML first and whitelists link schemes, so a
        // third-party README cannot inject markup.
        <div className="dc-markdown" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <p className="dc-prose">{body.slice(0, BODY_CHARS)}</p>
      )}
      {truncated && (
        <p className="dc-meta">Truncated for display — open it upstream for the rest.</p>
      )}
    </section>
  );
}

function useBusy(): [string | null, (label: string, run: () => Promise<void>) => void] {
  const [busy, setBusy] = useState<string | null>(null);
  const go = (label: string, run: () => Promise<void>) => {
    setBusy(label);
    run()
      .catch((err: unknown) =>
        toastsStore.add(
          'error',
          `${label} failed`,
          err instanceof Error ? err.message : String(err),
          6000,
        ),
      )
      .finally(() => setBusy(null));
  };
  return [busy, go];
}

function saveLibrary(): string {
  return getSetting<string>('browser.saveLibrary') || 'default';
}

function SourceActions({
  source,
  kind,
  item,
}: {
  source: string;
  kind: string;
  item: DiscoverItem;
}) {
  const [busy, run] = useBusy();
  const modelRepo = useSetting<string>('interpretability.modelRepo');

  if (source === 'hf' && kind === 'model') {
    const pinned = modelRepo === item.id;
    return (
      <button
        type="button"
        className="dc-action"
        disabled={pinned}
        title="Drives exact token counts and the model explorer"
        onClick={() => void setSetting('interpretability.modelRepo', item.id)}
      >
        {pinned ? <IconCheck /> : <IconPin />}{' '}
        {pinned ? 'Pinned as model repo' : 'Pin as model repo'}
      </button>
    );
  }
  if (source === 'hf' && kind === 'dataset') {
    return (
      <button
        type="button"
        className="dc-action"
        title="Peek rows, check the format and register it"
        onClick={() => void registry.runCommand('datasets.open')}
      >
        Inspect in Datasets
      </button>
    );
  }
  if (source === 'papers' || source === 'arxiv') {
    const save = (open: boolean) =>
      run(open ? 'Open PDF' : 'Save', async () => {
        const res = await saveArxivPaper(item.id, saveLibrary());
        toastsStore.add('success', 'Saved to the library', res.source.title, 3000);
        if (open) {
          registry.openPanel('research.pdfViewer', {
            instanceId: `pdf:${res.artifact.id}`,
            params: { artifactId: res.artifact.id, sourceId: res.source.id },
          });
        }
      });
    return (
      <>
        <button
          type="button"
          className="dc-action"
          disabled={busy !== null}
          onClick={() => save(true)}
        >
          {busy === 'Open PDF' ? 'Downloading…' : 'Open PDF'}
        </button>
        <button
          type="button"
          className="dc-action"
          disabled={busy !== null}
          onClick={() => save(false)}
        >
          <IconPlus /> {busy === 'Save' ? 'Saving…' : 'Save to library'}
        </button>
      </>
    );
  }
  if (source === 'mcp') {
    return (
      <button
        type="button"
        className="dc-action"
        title={`Installs with secrets handled — search for ${item.id} there`}
        onClick={() => void registry.runCommand('mcp.discover')}
      >
        Install via MCP pane
      </button>
    );
  }
  if (source === 'plugins') {
    return (
      <button
        type="button"
        className="dc-action"
        onClick={() => void registry.runCommand('marketplace.open')}
      >
        Manage in Marketplace
      </button>
    );
  }
  if (source === 'skills') {
    const installed = item.badges.some((b) => b.label === 'installed');
    if (installed) {
      return (
        <button
          type="button"
          className="dc-action"
          onClick={() => void registry.runCommand('skills.open')}
        >
          <IconCheck /> Installed — open Skills
        </button>
      );
    }
    return (
      <button
        type="button"
        className="dc-action"
        disabled={busy !== null}
        title="Copies the whole skill directory into your user skills"
        onClick={() =>
          run('Install', async () => {
            const res = await installSkill(item.id);
            toastsStore.add('success', `Installed ${res.name}`, `${res.files} files copied`, 4000);
            refresh(source, kind);
          })
        }
      >
        <IconPlus /> {busy ? 'Installing…' : 'Install skill'}
      </button>
    );
  }
  if (source === 'docs' && kind === 'entry' && item.url) {
    const url = item.url;
    return (
      <button
        type="button"
        className="dc-action"
        onClick={() => registry.openPanel('browser.view', { params: { url } })}
      >
        Read in app
      </button>
    );
  }
  if (source === 'docs' && kind === 'set') {
    if (item.id.startsWith('hf:') && item.url) {
      const url = item.url;
      const captured = item.badges.some((b) => b.label.startsWith('captured'));
      return captured ? (
        <button
          type="button"
          className="dc-action"
          onClick={() => void registry.runCommand('docviewer.open')}
        >
          Open in doc viewer
        </button>
      ) : (
        <button
          type="button"
          className="dc-action"
          disabled={busy !== null}
          onClick={() =>
            run('Capture', async () => {
              await captureDocSet(url, item.title);
              toastsStore.add(
                'success',
                'Capturing doc set',
                `${item.title} — it becomes searchable as pages land`,
                5000,
              );
              void registry.runCommand('docviewer.open');
            })
          }
        >
          {busy ? 'Starting…' : 'Capture to search offline'}
        </button>
      );
    }
    if (item.id.startsWith('captured:')) {
      return (
        <button
          type="button"
          className="dc-action"
          onClick={() => void registry.runCommand('docviewer.open')}
        >
          Open in doc viewer
        </button>
      );
    }
    return (
      <button
        type="button"
        className="dc-action"
        onClick={() => {
          setFilter('docs', 'entry', 'set', item.id);
          setActiveKind('docs', 'entry');
        }}
      >
        Search this set
      </button>
    );
  }
  return null;
}
