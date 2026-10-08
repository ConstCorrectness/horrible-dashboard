/**
 * Web apps in a site (`apps/<name>/`): the gallery to make or import one, and the
 * preview of one — the website at desktop, tablet or phone width, reloading as its
 * files change, with its console beside it.
 *
 * The frame runs on the apps origin (`scrive-apps.localhost`, see render/AppFrame.tsx),
 * which is also what a reader's browser gives the published copy: its own origin, its
 * own storage, WebGPU.
 */
import { useEffect, useState } from 'react';

import { openExternal } from '../../../external';
import { usePaneParams } from '../../../panes';
import { createApp, importSpaceApp, listApps, listAppTemplates, type AppInfo } from '../api';
import { ExternalIcon, PlusIcon, RefreshIcon } from '../icons';
import { openApp } from '../open';
import { ConsoleList, useAppFrame } from '../render/AppFrame';
import { useCurrentSite } from '../state';
import '../scrive.css';
import './apps.css';

const VIEWPORTS = [
  { id: 'fill', label: 'Fill', width: null },
  { id: 'desktop', label: 'Desktop 1280', width: 1280 },
  { id: 'tablet', label: 'Tablet 768', width: 768 },
  { id: 'phone', label: 'Phone 375', width: 375 },
] as const;

const KB = 1024;
function size(bytes: number): string {
  if (bytes < KB) return `${bytes} B`;
  if (bytes < KB * KB) return `${(bytes / KB).toFixed(0)} KB`;
  return `${(bytes / KB / KB).toFixed(1)} MB`;
}

export function AppPanel() {
  const params = usePaneParams();
  const current = useCurrentSite();
  const site = String(params.site ?? '') || current || '';
  const app = String(params.app ?? '');
  if (!site)
    return <p className="scrive-meta scrive-apps-pad">Pick a site in the Scrive pane first.</p>;
  return app ? (
    <AppPreview key={`${site}/${app}`} site={site} name={app} />
  ) : (
    <AppGallery site={site} />
  );
}

// ── one app ──────────────────────────────────────────────────────────────────

function AppPreview({ site, name }: { site: string; name: string }) {
  const app = useAppFrame(site, name);
  const [viewport, setViewport] = useState<(typeof VIEWPORTS)[number]['id']>('fill');
  const [showConsole, setShowConsole] = useState(true);
  const width = VIEWPORTS.find((v) => v.id === viewport)?.width ?? null;
  const errors = app.lines.filter((l) => l.level === 'error').length;

  return (
    <div className="scrive-apps">
      <header className="scrive-apps-head">
        <button type="button" className="scrive-apps-crumb" onClick={() => openApp(site)}>
          {site} / apps
        </button>
        <span className="scrive-apps-title">{name}</span>
        <div className="scrive-apps-viewports" role="radiogroup" aria-label="Viewport">
          {VIEWPORTS.map((v) => (
            <button
              key={v.id}
              type="button"
              role="radio"
              aria-checked={viewport === v.id}
              className={`scrive-apps-chip${viewport === v.id ? ' is-on' : ''}`}
              onClick={() => setViewport(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>
        <span className="scrive-apps-actions">
          <button
            type="button"
            className="scrive-apps-chip"
            onClick={app.reload}
            disabled={!app.url}
          >
            <RefreshIcon size={12} /> Reload
          </button>
          <button
            type="button"
            className="scrive-apps-chip"
            onClick={() => app.url && void openExternal(app.url)}
            disabled={!app.url}
          >
            <ExternalIcon size={12} /> Browser
          </button>
          <button
            type="button"
            className={`scrive-apps-chip${errors ? ' is-bad' : showConsole ? ' is-on' : ''}`}
            onClick={() => setShowConsole((v) => !v)}
            aria-expanded={showConsole}
          >
            Console{errors ? ` · ${errors}` : ''}
          </button>
        </span>
      </header>
      <div className={`scrive-apps-body${showConsole ? ' has-console' : ''}`}>
        <div className="scrive-apps-stage">
          {app.url ? (
            <iframe
              ref={app.frame}
              src={app.url}
              title={`Web app ${name}`}
              className="scrive-apps-frame"
              style={width ? { width, flex: 'none' } : undefined}
              sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals"
              allow="cross-origin-isolated; fullscreen; clipboard-write"
            />
          ) : (
            <p className="scrive-meta scrive-apps-pad">{app.origin?.reason ?? 'Starting…'}</p>
          )}
        </div>
        {showConsole && (
          <aside className="scrive-apps-console">
            <div className="scrive-apps-label">Console</div>
            <ConsoleList lines={app.lines} onClear={app.clear} />
          </aside>
        )}
      </div>
    </div>
  );
}

// ── the gallery ──────────────────────────────────────────────────────────────

function AppGallery({ site }: { site: string }) {
  const [apps, setApps] = useState<AppInfo[] | null>(null);
  const [templates, setTemplates] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [template, setTemplate] = useState('blank');
  const [space, setSpace] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [imported, setImported] = useState<AppInfo | null>(null);

  const refresh = () =>
    listApps(site).then(setApps, (e: unknown) => {
      setApps([]);
      setNote({ text: String(e instanceof Error ? e.message : e), bad: true });
    });

  useEffect(() => {
    void refresh();
    listAppTemplates().then(setTemplates, () => setTemplates(['blank']));
  }, [site]);

  const make = async () => {
    const clean = name.trim();
    if (!clean) return;
    setBusy('create');
    setNote(null);
    try {
      const made = await createApp(site, clean, template);
      setName('');
      await refresh();
      openApp(site, made.name);
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), bad: true });
    } finally {
      setBusy(null);
    }
  };

  const importIt = async () => {
    const id = space.trim();
    if (!id) return;
    setBusy('import');
    setNote({ text: `Copying ${id} from the Hub…` });
    setImported(null);
    try {
      const made = await importSpaceApp(site, id);
      setSpace('');
      setImported(made);
      setNote({
        text: `Imported as apps/${made.name} (${made.files} files, ${size(made.bytes)}).`,
      });
      await refresh();
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), bad: true });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="scrive-apps scrive-apps-gallery">
      <header className="scrive-apps-head">
        <span className="scrive-apps-title">{site} · web apps</span>
        <span className="scrive-meta">
          apps/&lt;name&gt;/ — static HTML, JS and CSS, embedded in a post with an {'{app}'} block
        </span>
      </header>

      <div className="scrive-apps-grid">
        <section className="scrive-apps-card">
          <div className="scrive-apps-label">New app</div>
          <div className="scrive-apps-row">
            <input
              type="text"
              aria-label="App name"
              placeholder="name, e.g. kernel-demo"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void make()}
            />
            <select
              aria-label="Template"
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
            >
              {templates.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void make()}
              disabled={!name.trim() || busy !== null}
            >
              Create
            </button>
          </div>
        </section>

        <section className="scrive-apps-card">
          <div className="scrive-apps-label">Import a Hugging Face Space</div>
          <div className="scrive-apps-row">
            <input
              type="text"
              aria-label="Space id or URL"
              placeholder="owner/name or huggingface.co/spaces/…"
              value={space}
              onChange={(e) => setSpace(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void importIt()}
            />
            <button
              type="button"
              onClick={() => void importIt()}
              disabled={!space.trim() || busy !== null}
            >
              Import
            </button>
          </div>
          <span className="scrive-meta">
            Static Spaces only. Model weights are left on the Hub; the app fetches them when it
            runs.
          </span>
        </section>
      </div>

      {note && <p className={`scrive-apps-note${note.bad ? ' is-bad' : ''}`}>{note.text}</p>}
      {imported && imported.skipped.length > 0 && (
        <details className="scrive-apps-skipped">
          <summary>{imported.skipped.length} files left out</summary>
          <ul>
            {imported.skipped.map((s) => (
              <li key={s.path}>
                <code>{s.path}</code> · {size(s.size)} · {s.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="scrive-apps-label scrive-apps-pad">In this site</div>
      {apps === null ? (
        <p className="scrive-meta scrive-apps-pad">Loading…</p>
      ) : apps.length === 0 ? (
        <p className="scrive-meta scrive-apps-pad">No apps yet.</p>
      ) : (
        <ul className="scrive-apps-list">
          {apps.map((a, i) => (
            <li key={a.name} style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}>
              <button
                type="button"
                className="scrive-apps-item"
                onClick={() => openApp(site, a.name)}
              >
                <span className="scrive-apps-item-title">{a.title}</span>
                <span className="scrive-meta">
                  apps/{a.name} · {a.files} files · {size(a.bytes)}
                  {a.hasIndex ? '' : ' · no index.html'}
                  {a.source
                    ? ` · from ${a.source.space}${a.source.license ? ` (${a.source.license})` : ''}`
                    : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="scrive-meta scrive-apps-pad">
        <PlusIcon size={12} /> Put one in a post: type <code>/</code> in the page and pick Web app.
      </p>
    </div>
  );
}
