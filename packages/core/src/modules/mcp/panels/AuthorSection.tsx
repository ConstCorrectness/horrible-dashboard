import { useCallback, useEffect, useState } from 'react';

import {
  createProject,
  deleteProject,
  listProjects,
  provisionProject,
  readProjectFile,
  registerProject,
  writeProjectFile,
  type McpProject,
  type McpTemplate,
} from '../api';

/**
 * Write an MCP server here, then run it here.
 *
 * The loop this closes: scaffold → provision → edit → save (which restarts the server)
 * → run a tool from the Servers section's Run tab → check it with the conformance
 * suite. Nothing in it leaves the app, which matters because the alternative loop —
 * editor, terminal, restart the client, hope — is where most of the friction in
 * writing an MCP server actually lives.
 *
 * A scaffolded project is registered as an ordinary stdio server, so it appears in the
 * Servers list like anything else and inherits the inspector, the wire transcript and
 * the cost view unchanged. This section owns only what is genuinely different: the
 * files, the toolchain, and the restart.
 */

function StateDot({ state }: { state: McpProject['state'] }) {
  const color =
    state === 'ready'
      ? 'var(--ok, #3fb950)'
      : state === 'error'
        ? 'var(--danger, #f85149)'
        : state === 'provisioning'
          ? 'var(--warn, #d29922)'
          : 'var(--text-dim)';
  return <span style={{ color }}>●</span>;
}

function FileEditor({ project, onSaved }: { project: McpProject; onSaved: () => void }) {
  const [path, setPath] = useState(project.entry);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (target: string) => {
      setLoading(true);
      setError(null);
      setStatus(null);
      try {
        setText((await readProjectFile(project.id, target)).text);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [project.id],
  );

  useEffect(() => {
    void load(path);
  }, [load, path]);

  const save = async () => {
    setError(null);
    try {
      const res = await writeProjectFile(project.id, path, text);
      // The restart result is the honest answer to "did my edit take": a save that
      // succeeded while the server failed to come back is the common case when you
      // just introduced a syntax error, and reporting only "Saved" would hide it.
      setStatus(
        res.restartError
          ? `Saved — but the server did not restart: ${res.restartError}`
          : res.restarted
            ? 'Saved and restarted.'
            : 'Saved.',
      );
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div style={{ marginTop: '0.4rem' }}>
      <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
        <select value={path} onChange={(e) => setPath(e.target.value)} style={{ flex: 1 }}>
          {project.files.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <button onClick={() => void save()} disabled={loading}>
          Save
        </button>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        rows={16}
        style={{
          width: '100%',
          marginTop: '0.3rem',
          fontFamily: 'monospace',
          fontSize: '0.7rem',
          whiteSpace: 'pre',
        }}
      />
      {status && (
        <div
          style={{
            fontSize: '0.68rem',
            color: status.includes('did not restart')
              ? 'var(--danger, #f85149)'
              : 'var(--ok, #3fb950)',
          }}
        >
          {status}
        </div>
      )}
      {error && <div style={{ fontSize: '0.68rem', color: 'var(--danger, #f85149)' }}>{error}</div>}
    </div>
  );
}

function ProjectCard({ project, onChanged }: { project: McpProject; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: '0.6rem 0.75rem',
        marginBottom: '0.5rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <StateDot state={project.state} />
        <strong>{project.title}</strong>
        <code style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
          mcp-{project.id} · {project.template}
        </code>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem' }}>
          {!project.registered && (
            <button disabled={busy} onClick={() => act(() => registerProject(project.id))}>
              Add to servers
            </button>
          )}
          {project.registered && project.state !== 'ready' && (
            <button disabled={busy} onClick={() => act(() => provisionProject(project.id))}>
              {busy ? 'Provisioning…' : 'Provision'}
            </button>
          )}
          <button disabled={busy} onClick={() => setOpen((v) => !v)}>
            {open ? 'Close' : 'Edit'}
          </button>
          {/* Removing unregisters the server and leaves the source alone. Deleting
              someone's code because they clicked a list-row button would be
              unrecoverable, and there is no undo here. */}
          {project.registered && (
            <button disabled={busy} onClick={() => act(() => deleteProject(project.id, false))}>
              Remove
            </button>
          )}
        </span>
      </div>

      <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
        <code>{project.root}</code>
      </div>

      {!project.registered && (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
          Removed from the server list; the source is still here.
        </div>
      )}
      {project.registered && project.state === 'new' && (
        <div style={{ fontSize: '0.7rem', color: 'var(--warn, #d29922)', marginTop: '0.25rem' }}>
          Not provisioned yet — its interpreter doesn't exist, so the server stays disabled.
        </div>
      )}
      {project.error && (
        <div style={{ fontSize: '0.7rem', color: 'var(--danger, #f85149)', marginTop: '0.25rem' }}>
          {project.error}
        </div>
      )}
      {error && (
        <div style={{ fontSize: '0.7rem', color: 'var(--danger, #f85149)', marginTop: '0.25rem' }}>
          {error}
        </div>
      )}

      {project.log.length > 0 && (
        <details style={{ fontSize: '0.68rem', marginTop: '0.3rem' }}>
          <summary style={{ color: 'var(--text-dim)', cursor: 'pointer' }}>
            Provisioning log ({project.log.length} lines)
          </summary>
          <pre
            style={{
              maxHeight: 160,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              margin: '0.2rem 0 0',
            }}
          >
            {project.log.join('\n')}
          </pre>
        </details>
      )}

      {open && <FileEditor project={project} onSaved={onChanged} />}
    </div>
  );
}

function NewProjectForm({
  hasUv,
  hasNpm,
  onCreated,
}: {
  hasUv: boolean;
  hasNpm: boolean;
  onCreated: () => void;
}) {
  const [id, setId] = useState('');
  const [title, setTitle] = useState('');
  const [template, setTemplate] = useState<McpTemplate>('python');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const missing = template === 'python' ? !hasUv : !hasNpm;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createProject(id.trim(), template, title.trim());
      setId('');
      setTitle('');
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{ borderTop: '1px solid var(--border)', paddingTop: '0.6rem', marginTop: '0.6rem' }}
    >
      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          placeholder="id (e.g. my-tools)"
          value={id}
          onChange={(e) => setId(e.target.value)}
          style={{ width: 150 }}
        />
        <input
          placeholder="title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          style={{ flex: 1, minWidth: 120 }}
        />
        <select value={template} onChange={(e) => setTemplate(e.target.value as McpTemplate)}>
          <option value="python">Python (FastMCP)</option>
          <option value="node">Node (TS SDK)</option>
        </select>
        <button disabled={busy || !id.trim()} onClick={() => void submit()}>
          Scaffold
        </button>
      </div>
      {/* Named before the project exists, not after it fails to build. */}
      {missing && (
        <div style={{ color: 'var(--warn, #d29922)', fontSize: '0.7rem', marginTop: '0.3rem' }}>
          {template === 'python'
            ? 'uv is not on PATH — install uv to provision a Python server.'
            : 'npm is not on PATH — install Node.js to provision a Node server.'}
        </div>
      )}
      {error && (
        <div style={{ color: 'var(--danger, #f85149)', fontSize: '0.7rem', marginTop: '0.3rem' }}>
          {error}
        </div>
      )}
    </div>
  );
}

export function AuthorSection({ onChanged }: { onChanged: () => void }) {
  const [projects, setProjects] = useState<McpProject[]>([]);
  const [toolchains, setToolchains] = useState({ hasUv: true, hasNpm: true });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await listProjects();
      setProjects(res.projects);
      setToolchains({ hasUv: res.hasUv, hasNpm: res.hasNpm });
      setError(null);
      // The Servers section shows the same servers from the other side, so anything
      // that changes a project has to invalidate it too.
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [onChanged]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '0.5rem' }}>
        Scaffold a server, provision its own environment, edit it here. Saving a source file
        restarts the running server, so the agent's tool list follows your edit.
      </div>
      {loading && <div style={{ color: 'var(--text-dim)' }}>Loading…</div>}
      {error && <div style={{ color: 'var(--danger, #f85149)' }}>{error}</div>}
      {!loading && projects.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>No projects yet.</div>
      )}
      {projects.map((p) => (
        <ProjectCard key={p.id} project={p} onChanged={refresh} />
      ))}
      <NewProjectForm hasUv={toolchains.hasUv} hasNpm={toolchains.hasNpm} onCreated={refresh} />
    </div>
  );
}
