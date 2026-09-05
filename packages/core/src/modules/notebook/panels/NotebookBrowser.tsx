import { useCallback, useEffect, useState } from 'react';

import { createNotebook, envStatus, listNotebooks, type NotebookFile } from '../api';
import { openNotebook } from '../open';

const dim = { color: 'var(--text-dim)' } as const;

/** Singleton pane: the notebook catalog under the `notebook.root` setting. */
export function NotebookBrowser() {
  const [files, setFiles] = useState<NotebookFile[]>([]);
  const [root, setRoot] = useState('');
  const [newName, setNewName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [envReady, setEnvReady] = useState<boolean | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await listNotebooks();
      setFiles(res.files);
      setRoot(res.root);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    void envStatus()
      .then((s) => setEnvReady(s.ready))
      .catch(() => setEnvReady(null));
  }, [refresh]);

  const open = useCallback((path: string) => {
    openNotebook(path);
  }, []);

  const create = useCallback(async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const nb = await createNotebook(name);
      setNewName('');
      await refresh();
      open(nb.path);
    } catch (e) {
      setError(String(e));
    }
  }, [newName, refresh, open]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        fontSize: 'var(--fs-body)',
      }}
    >
      <div style={{ padding: '0.5rem', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', gap: '0.35rem' }}>
          <input
            value={newName}
            placeholder="new-notebook.ipynb"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && create()}
            style={{ flex: 1 }}
          />
          <button onClick={create} disabled={!newName.trim()}>
            Create
          </button>
        </div>
        {/* A root path and a bootstrap note are metadata about the list, not the
            list — the `telemetry` step, and mono because one of them is a path. */}
        <div
          style={{
            marginTop: '0.35rem',
            fontSize: 'var(--fs-meta)',
            fontFamily: 'var(--font-mono, monospace)',
            ...dim,
          }}
        >
          {root && <div title="notebook.root setting">📁 {root}</div>}
          {envReady === false && (
            <div>Kernel venv bootstraps on first run (ipykernel + ipywidgets).</div>
          )}
        </div>
      </div>
      {error && (
        <div style={{ padding: '0.4rem 0.5rem', color: 'var(--danger)' }}>{error}</div>
      )}
      <div style={{ flex: 1, overflow: 'auto', padding: '0.25rem' }}>
        {files.length === 0 && <div style={{ padding: '0.5rem', ...dim }}>No notebooks yet.</div>}
        {files.map((f) => (
          <button
            key={f.path}
            onClick={() => open(f.path)}
            style={{
              display: 'block',
              width: '100%',
              textAlign: 'left',
              padding: '0.3rem 0.4rem',
              background: 'transparent',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              color: 'inherit',
            }}
            title={f.path}
          >
            📓 {f.path}
          </button>
        ))}
      </div>
    </div>
  );
}
