import { useCallback, useEffect, useRef, useState } from 'react';

import {
  createProject,
  fetchProjectData,
  listProjects,
  listProviders,
  resolveEnvironment,
  searchEnvironments,
  type EnvironmentRef,
  type Project,
  type ProviderInfo,
} from '../api';
import { ownedReason, projectNote, subscribeProjectNotes } from '../activity';
import { onTrainingEvent } from '../client';
import { openTrainingNotebook } from '../open';
import { openContextMenu } from '../../../overlay/context-menu';
import './projects.css';

const dim = { color: 'var(--text-dim)' } as const;

/**
 * The one-line "is this the dataset everyone uses, or someone's 5k dump?" signal.
 *
 * Every provider already returns it — HF sends `downloads`/`likes`, Kaggle sends
 * `size` on a dataset and `deadline`/`reward` on a competition — and the pane used
 * to drop the whole `meta` bag on the floor, rendering `id (kind)` and nothing
 * else. A search for "reasoning" then lists twenty plausible-looking ids in an
 * order that is not popularity, with no way to tell them apart short of opening
 * huggingface.co in another window.
 *
 * Deliberately generic rather than a switch on `provider`: a plugin provider's
 * `meta` is whatever it chose, and showing its keys beats showing nothing. Only
 * keys with a value are rendered, so a missing count is absent rather than "0".
 */
const signalStyle = {
  marginLeft: '0.45rem',
  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
  fontSize: 10,
  color: 'var(--text-dim)',
} as const;

const exactBadge = {
  marginLeft: '0.4rem',
  fontSize: 9,
  fontWeight: 700,
  letterSpacing: '0.1em',
  textTransform: 'uppercase' as const,
  color: 'var(--accent, #3b82f6)',
  border: '1px solid var(--accent, #3b82f6)',
  borderRadius: 3,
  padding: '0 4px',
};

const SIGNAL_KEYS = ['downloads', 'likes', 'size', 'rows', 'deadline', 'reward', 'namespace'];

function refSignal(ref: EnvironmentRef): string {
  const parts: string[] = [];
  for (const key of SIGNAL_KEYS) {
    const value = ref.meta?.[key];
    if (value === undefined || value === null || value === '' || value === 0) continue;
    const shown = typeof value === 'number' ? compact(value) : String(value);
    parts.push(key === 'downloads' ? `↓${shown}` : key === 'likes' ? `♥${shown}` : shown);
  }
  return parts.join(' · ');
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** The marker on a project another module owns. Same treatment as a read-only
 * bundled eval suite: uppercase, bordered, muted — it explains why the authoring
 * actions in its menu are off. */
const ownedBadge = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: '0.08em',
  textTransform: 'uppercase' as const,
  color: 'var(--text-dim)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  padding: '1px 5px',
};

/**
 * The training hub: search an environment provider (Kaggle / HF / Gymnasium /
 * plugins), create a project from a result, watch venv/data progress live, and
 * jump into the project's notebook.
 */
export function ProjectsPane() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState('kaggle');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<EnvironmentRef[]>([]);
  const [searching, setSearching] = useState(false);
  /** The id `resolve` matched exactly, so the row can say why it is first. */
  const [exactId, setExactId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState<string | null>(null);
  const [progress, setProgress] = useState<Record<string, string>>({});
  /** Repaint when a menu action writes a note; the notes themselves live in
   *  `activity.ts`, which is module state rather than component state. */
  const [, setNoteTick] = useState(0);
  const logRef = useRef<Record<string, string[]>>({});

  const refresh = useCallback(() => {
    listProjects()
      .then((r) => setProjects(r.projects))
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    listProviders()
      .then((r) => setProviders(r.providers))
      .catch((e: Error) => setError(e.message));
    refresh();
  }, [refresh]);

  useEffect(() => {
    const record = (data: { projectId: string; line: string }) => {
      const log = logRef.current[data.projectId] ?? [];
      logRef.current[data.projectId] = [...log.slice(-199), data.line];
      setProgress((p) => ({ ...p, [data.projectId]: data.line }));
    };
    const unsubs = [
      onTrainingEvent('env_progress', record),
      onTrainingEvent('fetch_progress', record),
      onTrainingEvent('project_changed', refresh),
      // The project actions live in a context menu now, declared in the module
      // manifest, so their progress lines arrive through the note store rather
      // than through this component's own state. See `activity.ts`.
      subscribeProjectNotes(() => setNoteTick((n) => n + 1)),
    ];
    return () => unsubs.forEach((u) => u());
  }, [refresh]);

  /**
   * Search, plus an **exact-id lookup running beside it**.
   *
   * Provider search is a fuzzy name match ranked by the provider, not by you:
   * `GAIR/LIMO` does not appear in a search for `LIMO` (HF returns `Limorgu/…`,
   * `Limour/…`), so a person who arrives already knowing which dataset they want —
   * the normal case for a paper's dataset — had no way to say so. The `resolve`
   * route has always existed and the agent has always used it (`training.
   * resolve_environment`); this pane simply never called it, which made the agent
   * strictly more capable than the UI at the very first step.
   *
   * Both requests go out together and the exact hit is prepended and marked, so a
   * typo still shows the fuzzy list rather than only an error. A resolve failure
   * is swallowed: for a free-text query ("reasoning") it is the expected answer,
   * and surfacing it would mean an error banner on every successful search.
   */
  const search = useCallback(() => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    const fuzzy = searchEnvironments(provider, q).then((r) => r.results);
    const exact = resolveEnvironment(provider, q).catch(() => null);
    Promise.all([fuzzy, exact])
      .then(([found, hit]) => {
        if (!hit) return setResults(found);
        setResults([hit, ...found.filter((r) => r.id !== hit.id)]);
        setExactId(hit.id);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSearching(false));
  }, [provider, query]);

  const create = useCallback(
    (ref: EnvironmentRef) => {
      setCreating(ref.id);
      setError(null);
      createProject({ provider: ref.provider, ref: ref.id, kind: ref.kind })
        .then((project) => {
          refresh();
          void fetchProjectData(project.id).catch(() => undefined);
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => setCreating(null));
    },
    [refresh],
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'auto' }}>
      <div style={{ padding: '0.5rem', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {providers.map((p) => (
              <option key={p.provider} value={p.provider}>
                {p.label}
              </option>
            ))}
          </select>
          <input
            style={{ flex: 1 }}
            value={query}
            placeholder="Search competitions, datasets, envs…"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
          />
          <button onClick={search} disabled={searching}>
            {searching ? '…' : 'Search'}
          </button>
        </div>
        {error && (
          <div
            style={{ color: 'var(--danger, #e5534b)', fontSize: '0.75rem', marginTop: '0.3rem' }}
          >
            {error}
          </div>
        )}
        {results.length > 0 && (
          <ul style={{ listStyle: 'none', margin: '0.5rem 0 0', padding: 0 }}>
            {results.map((r) => (
              <li
                key={`${r.provider}:${r.id}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.25rem 0',
                  borderBottom: '1px solid var(--border)',
                  fontSize: '0.8rem',
                }}
              >
                <span
                  style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {r.title || r.id} <span style={dim}>({r.kind})</span>
                  {r.id === exactId && <span style={exactBadge}>exact</span>}
                  {refSignal(r) && (
                    <span style={signalStyle} title="Reported by the provider">
                      {refSignal(r)}
                    </span>
                  )}
                </span>
                <button onClick={() => create(r)} disabled={creating !== null}>
                  {creating === r.id ? 'Creating…' : 'Create project'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div style={{ flex: 1, padding: '0.5rem' }}>
        <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', ...dim }}>Projects</div>
        {projects.length === 0 && (
          <div style={{ fontSize: '0.8rem', marginTop: '0.5rem', ...dim }}>
            No projects yet — search a provider above to start one.
          </div>
        )}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {projects.map((p) => {
            const note = progress[p.id] || projectNote(p.id);
            const menu = (e: { clientX: number; clientY: number }): void => {
              openContextMenu(e, {
                kind: 'training.project',
                projectId: p.id,
                name: p.name,
                owner: p.owner ?? null,
              });
            };
            return (
              <li key={p.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', alignItems: 'stretch' }}>
                  {/* The row *is* the primary action. Classed, because a classless
                      <button> is the 30px action control and this is a two-line
                      row; see the One Height Rule in CLAUDE.md. */}
                  <button
                    className="training-project-row"
                    disabled={!!p.owner}
                    title={p.owner ? ownedReason(p.owner) : `Open ${p.name} · right-click for more`}
                    onClick={() => openTrainingNotebook(p.id, 'main.ipynb')}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      menu(e);
                    }}
                  >
                    <span className="training-project-line">
                      <strong className="training-project-name">{p.name}</strong>
                      {/* Marked, not hidden — the same call the bundled eval suites
                          make. A project missing from this list is a directory
                          eating disk that you can neither see nor delete. */}
                      {p.owner && (
                        <span style={ownedBadge} title={ownedReason(p.owner)}>
                          {p.owner}
                        </span>
                      )}
                      <span className="training-project-status">
                        {p.venv_ready ? 'venv ✓' : 'venv…'} · {p.data_ready ? 'data ✓' : 'data…'}
                      </span>
                    </span>
                    <span className="training-project-meta">
                      {p.refs.map((r) => `${r.provider}:${r.id}`).join(', ')}
                    </span>
                    {note && <span className="training-project-note">{note}</span>}
                  </button>
                  {/* The same menu, without a right-click. A context menu nobody
                      knows is there is the actions being gone. */}
                  <button
                    className="training-project-more"
                    title="Recipe, push, delete…"
                    aria-label={`Actions for ${p.name}`}
                    onClick={(e) => {
                      const r = e.currentTarget.getBoundingClientRect();
                      menu({ clientX: r.left, clientY: r.bottom });
                    }}
                  >
                    ⋯
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
