import { useCallback, useEffect, useRef, useState } from 'react';

import {
  createProject,
  deleteProject,
  fetchProjectData,
  listProjects,
  listProviders,
  pushProject,
  searchEnvironments,
  type EnvironmentRef,
  type Project,
  type ProviderInfo,
} from '../api';
import { onTrainingEvent } from '../client';
import { openTrainingNotebook, openTrainingRecipe } from '../open';

const dim = { color: 'var(--text-dim)' } as const;

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
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState<string | null>(null);
  const [progress, setProgress] = useState<Record<string, string>>({});
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
    ];
    return () => unsubs.forEach((u) => u());
  }, [refresh]);

  const search = useCallback(() => {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    searchEnvironments(provider, query)
      .then((r) => setResults(r.results))
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
          {projects.map((p) => (
            <li key={p.id} style={{ padding: '0.4rem 0', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <strong style={{ flex: 1, minWidth: 0, fontSize: '0.85rem' }}>{p.name}</strong>
                <span style={{ fontSize: '0.7rem', ...dim }}>
                  {p.venv_ready ? 'venv ✓' : 'venv…'} · {p.data_ready ? 'data ✓' : 'data…'}
                </span>
                <button onClick={() => openTrainingNotebook(p.id, 'main.ipynb')}>
                  Open notebook
                </button>
                <button
                  title="Fine-tuning recipe: a typed form that writes cells into this project's notebook"
                  onClick={() => openTrainingRecipe(p.id)}
                >
                  🧪 Recipe
                </button>
                <button
                  title="Push notebook to Kaggle kernels"
                  onClick={() => {
                    setProgress((prog) => ({ ...prog, [p.id]: 'pushing to Kaggle…' }));
                    pushProject(p.id, 'kaggle')
                      .then((r) =>
                        setProgress((prog) => ({
                          ...prog,
                          [p.id]: r.url ? `pushed → ${r.url}` : `push: ${r.status}`,
                        })),
                      )
                      .catch((e: Error) =>
                        setProgress((prog) => ({ ...prog, [p.id]: `push failed: ${e.message}` })),
                      );
                  }}
                >
                  ⇪ Kaggle
                </button>
                <button
                  title="Push notebook to Google Colab (via Drive)"
                  onClick={() => {
                    setProgress((prog) => ({ ...prog, [p.id]: 'pushing to Colab…' }));
                    pushProject(p.id, 'colab')
                      .then((r) =>
                        setProgress((prog) => ({
                          ...prog,
                          [p.id]: r.url ? `pushed → ${r.url}` : `push: ${r.status}`,
                        })),
                      )
                      .catch((e: Error) =>
                        setProgress((prog) => ({ ...prog, [p.id]: `push failed: ${e.message}` })),
                      );
                  }}
                >
                  ⇪ Colab
                </button>
                <button
                  title="Delete project (removes venv and data)"
                  onClick={() => {
                    void deleteProject(p.id).then(refresh);
                  }}
                >
                  ✕
                </button>
              </div>
              <div style={{ fontSize: '0.7rem', ...dim }}>
                {p.refs.map((r) => `${r.provider}:${r.id}`).join(', ')}
              </div>
              {progress[p.id] && (
                <div
                  style={{
                    fontSize: '0.7rem',
                    fontFamily: 'var(--font-mono, monospace)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    ...dim,
                  }}
                >
                  {progress[p.id]}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
