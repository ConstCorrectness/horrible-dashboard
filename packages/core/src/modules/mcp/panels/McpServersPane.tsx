import { useCallback, useEffect, useState } from 'react';

import { usePaneSection } from '../../../layout/use-sections';
import {
  connectServer,
  deleteServer,
  disconnectServer,
  listServers,
  saveServer,
  type McpServer,
  type McpServerInput,
  type McpTransport,
} from '../api';
import { AuthorSection } from './AuthorSection';
import { DiscoverSection } from './DiscoverSection';
import { ServerInspector } from './ServerInspector';

/**
 * The MCP servers pane: what's configured, what's connected, and — when something is
 * broken — why.
 *
 * The "why" is the point. A server that won't start is the normal failure here (a
 * command not on PATH, a bad URL, a server that crashes on boot), so the pane shows the
 * resolved argv and the error text rather than a bare red dot. Connection state is
 * reported by the backend, which owns the session; this pane never holds one.
 */

const EMPTY: McpServerInput = { id: '', name: '', transport: 'stdio', command: '', args: [] };

function stateColor(state: McpServer['state']): string {
  if (state === 'ready') return 'var(--ok, #3fb950)';
  if (state === 'error') return 'var(--danger, #f85149)';
  if (state === 'starting') return 'var(--warn, #d29922)';
  return 'var(--text-dim)';
}

/**
 * Whose code this row runs.
 *
 * It reads as decoration and isn't: `registry` means a third party's package is
 * executing on this machine with the user's environment, which is a materially
 * different thing from a server they wrote in the Author section. The label is the
 * only place that distinction is ever visible after the moment of adding.
 */
function OriginChip({ origin }: { origin: McpServer['origin'] }) {
  if (origin === 'manual') return null;
  const registry = origin === 'registry';
  return (
    <span
      style={{
        fontSize: '0.62rem',
        padding: '0 0.3rem',
        borderRadius: 3,
        border: '1px solid var(--border)',
        color: registry ? 'var(--warn, #d29922)' : 'var(--text-dim)',
      }}
      title={
        registry
          ? 'Third-party code installed from the MCP registry.'
          : 'A project you scaffolded in the Author section.'
      }
    >
      {registry ? 'third-party' : 'yours'}
    </span>
  );
}

function ServerCard({ server, onChanged }: { server: McpServer; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const unavailable = server.transport === 'stdio' && !server.target.available;

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
        <span style={{ color: stateColor(server.state) }}>●</span>
        <strong>{server.name || server.id}</strong>
        <code style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>{server.group}</code>
        <OriginChip origin={server.origin} />
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem' }}>
          <button disabled={busy} onClick={() => act(() => connectServer(server.id))}>
            {server.state === 'ready' ? 'Reconnect' : 'Connect'}
          </button>
          {server.state === 'ready' && (
            <button disabled={busy} onClick={() => act(() => disconnectServer(server.id))}>
              Disconnect
            </button>
          )}
          <button disabled={busy} onClick={() => act(() => deleteServer(server.id))}>
            Remove
          </button>
        </span>
      </div>

      <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: '0.3rem' }}>
        {server.transport === 'stdio' ? (
          <code>{(server.target.argv ?? [server.command, ...server.args]).join(' ')}</code>
        ) : (
          <code>
            {server.transport.toUpperCase()} {server.url}
            {server.hasToken ? ' · authenticated' : ''}
          </code>
        )}
      </div>

      {unavailable && (
        <div style={{ fontSize: '0.72rem', color: 'var(--danger, #f85149)', marginTop: '0.3rem' }}>
          `{server.command}` is not on PATH on this machine.
        </div>
      )}
      {server.error && (
        <div style={{ fontSize: '0.72rem', color: 'var(--danger, #f85149)', marginTop: '0.3rem' }}>
          {server.error}
        </div>
      )}

      {/* A server that starts but has no credential fails inside the *server*, whose
          error text is rarely legible. Saying it here turns that into a fixable
          state rather than a mystery. */}
      {server.missingSecretEnv.length > 0 && (
        <div style={{ fontSize: '0.72rem', color: 'var(--warn, #d29922)', marginTop: '0.3rem' }}>
          Needs a value for {server.missingSecretEnv.join(', ')}.
        </div>
      )}

      {/* The wire is available whenever anything has been recorded — including for a
          server that never reached `ready`, which is exactly when it is worth
          reading. */}
      <div style={{ marginTop: '0.4rem', fontSize: '0.75rem' }}>
        <button
          onClick={() => setOpen((v) => !v)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-dim)',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          {open ? '▾' : '▸'}{' '}
          {server.state === 'ready'
            ? `${server.tools.length} tools · ${server.prompts.length} prompts · ${server.resources.length} resources`
            : 'Inspect'}
        </button>
        {open && <ServerInspector server={server} />}
      </div>
    </div>
  );
}

function AddServerForm({ onAdded }: { onAdded: () => void }) {
  const [draft, setDraft] = useState<McpServerInput>(EMPTY);
  const [argsText, setArgsText] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveServer({
        ...draft,
        // Whitespace-split is what a user pastes from a README command line.
        args: argsText.trim() ? argsText.trim().split(/\s+/) : [],
        ...(token ? { token } : {}),
      });
      setDraft(EMPTY);
      setArgsText('');
      setToken('');
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const stdio = draft.transport === 'stdio';

  return (
    <div
      style={{ borderTop: '1px solid var(--border)', paddingTop: '0.6rem', marginTop: '0.6rem' }}
    >
      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          placeholder="id (e.g. filesystem)"
          value={draft.id}
          onChange={(e) => setDraft({ ...draft, id: e.target.value })}
          style={{ width: 150 }}
        />
        <select
          value={draft.transport}
          onChange={(e) => setDraft({ ...draft, transport: e.target.value as McpTransport })}
        >
          <option value="stdio">stdio</option>
          <option value="http">http</option>
          <option value="sse">sse</option>
        </select>
        {stdio ? (
          <>
            <input
              placeholder="command (e.g. npx)"
              value={draft.command ?? ''}
              onChange={(e) => setDraft({ ...draft, command: e.target.value })}
              style={{ width: 130 }}
            />
            <input
              placeholder="args"
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              style={{ flex: 1, minWidth: 180 }}
            />
          </>
        ) : (
          <>
            <input
              placeholder="https://server.example/mcp"
              value={draft.url ?? ''}
              onChange={(e) => setDraft({ ...draft, url: e.target.value })}
              style={{ flex: 1, minWidth: 180 }}
            />
            <input
              type="password"
              placeholder="bearer token (optional)"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              style={{ width: 160 }}
            />
          </>
        )}
        <button disabled={busy || !draft.id} onClick={submit}>
          Add
        </button>
      </div>
      {error && (
        <div style={{ color: 'var(--danger, #f85149)', fontSize: '0.72rem', marginTop: '0.3rem' }}>
          {error}
        </div>
      )}
      <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem', marginTop: '0.3rem' }}>
        Tools become <code>mcp-&lt;id&gt;.&lt;tool&gt;</code> and load on demand as the{' '}
        <code>mcp-&lt;id&gt;</code> group. Tokens are stored encrypted server-side.
      </div>
    </div>
  );
}

export function McpServersPane() {
  const { section } = usePaneSection();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await listServers();
      setServers(res.servers);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (section === 'discover') {
    return (
      <div style={{ padding: '0.75rem', height: '100%', overflow: 'auto' }}>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '0.5rem' }}>
          The official MCP registry, with a shipped shortlist in front of it. Inspect before you
          add: what a server <em>is</em> comes from the running server, not from its listing.
        </div>
        <DiscoverSection onAdded={refresh} />
      </div>
    );
  }

  if (section === 'author') {
    return (
      <div style={{ padding: '0.75rem', height: '100%', overflow: 'auto' }}>
        <AuthorSection onChanged={refresh} />
      </div>
    );
  }

  return (
    <div style={{ padding: '0.75rem', height: '100%', overflow: 'auto' }}>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '0.5rem' }}>
        MCP servers extend the agent with third-party tools, prompts and resources.
      </div>
      {loading && <div style={{ color: 'var(--text-dim)' }}>Loading…</div>}
      {error && <div style={{ color: 'var(--danger, #f85149)' }}>{error}</div>}
      {!loading && servers.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
          No servers configured yet.
        </div>
      )}
      {servers.map((s) => (
        <ServerCard key={s.id} server={s} onChanged={refresh} />
      ))}
      <AddServerForm onAdded={refresh} />
    </div>
  );
}
