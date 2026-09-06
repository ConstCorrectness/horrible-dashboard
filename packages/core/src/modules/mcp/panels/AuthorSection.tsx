import { useCallback, useEffect, useState } from 'react';

import { Button, Chip } from '../../../Primitives';
import { dialogs } from '../../../dialogs';

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

/**
 * Edit a scaffolded server's source, in place.
 *
 * **Why this is not the real editor.** The obvious improvement is to open the file
 * in `editor.buffer` — this app has a full CodeMirror editor with LSP, and this is
 * a bare textarea. But a save *here* also restarts the server
 * (`writeProjectFile` -> provision -> restart), and that is the entire reason the
 * inline editor is usable: you change a tool's schema and the next agent call sees
 * it. The editor module writes through the files module, which knows nothing about
 * MCP, so moving the textarea would trade a worse text control for a broken
 * feedback loop — you would save, and the running server would keep serving the old
 * code with nothing saying so.
 *
 * Making that swap correctly needs a watcher on the project directory, which this
 * module's own roadmap already names as the missing piece. Until then the honest
 * fix is the one applied here: keep the restart-on-save, and repair what was
 * actually wrong with the control — no dirty indicator, and no warning before
 * discarding an edit.
 */
function FileEditor({ project, onSaved }: { project: McpProject; onSaved: () => void }) {
  const [path, setPath] = useState(project.entry);
  const [text, setText] = useState('');
  // The last text the backend gave us. Comparing against it is what makes "dirty"
  // real rather than "has been focused" — retyping a character back to its original
  // value correctly reports clean.
  const [saved, setSaved] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dirty = text !== saved;

  const load = useCallback(
    async (target: string) => {
      setLoading(true);
      setError(null);
      setStatus(null);
      try {
        const next = (await readProjectFile(project.id, target)).text;
        setText(next);
        setSaved(next);
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

  /** Switching files throws away unsaved work, so ask first. */
  const switchTo = async (next: string) => {
    if (next === path) return;
    if (dirty) {
      const ok = await dialogs.confirm({
        title: `Discard changes to ${path}?`,
        message: 'The edits in this file have not been saved and will be lost.',
        confirmLabel: 'Discard',
        danger: true,
      });
      if (!ok) return;
    }
    setPath(next);
  };

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
      setSaved(text);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div style={{ marginTop: 'var(--space-3)' }}>
      <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
        <select
          value={path}
          onChange={(e) => void switchTo(e.target.value)}
          style={{
            flex: 1,
            minWidth: 0,
            background: 'var(--bg-inset)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            // Horizontal padding only: `controls.css` fixes this control's height and
            // strips its vertical padding (the One Height Rule) — see theming.mdx.
            padding: '0 var(--space-3)',
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--fs-meta)',
          }}
        >
          {project.files.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        {dirty && <Chip kind="warn">unsaved</Chip>}
        <Button
          intent={dirty ? 'primary' : 'default'}
          size="sm"
          onClick={() => void save()}
          disabled={loading || !dirty}
          title={dirty ? 'Save and restart the server' : 'No changes to save'}
        >
          Save
        </Button>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        rows={16}
        style={{
          width: '100%',
          boxSizing: 'border-box',
          marginTop: 'var(--space-2)',
          background: 'var(--bg-inset)',
          color: 'var(--text)',
          border: `1px solid ${dirty ? 'var(--warn)' : 'var(--border)'}`,
          borderRadius: 'var(--radius-sm)',
          padding: 'var(--space-3)',
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--fs-meta)',
          lineHeight: 1.5,
          whiteSpace: 'pre',
        }}
      />
      <div style={{ fontSize: 'var(--fs-micro)', color: 'var(--text-faint)' }}>
        Saving restarts the server, so the next agent call sees the change.
      </div>
      {status && (
        <div
          style={{
            fontSize: 'var(--fs-meta)',
            color: status.includes('did not restart') ? 'var(--danger)' : 'var(--success)',
          }}
        >
          {status}
        </div>
      )}
      {error && <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>{error}</div>}
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
