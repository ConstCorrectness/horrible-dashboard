import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { DataList, DataRow, type RowKind } from '../../../DataList';
import { dialogs } from '../../../dialogs';
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
import { ExportSection } from './ExportSection';
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

/**
 * A connection state as a row verdict.
 *
 * Mapped onto the shared `RowKind` vocabulary rather than a private colour table,
 * so "ready" is the same green here as a passing eval row and a reader learns the
 * palette once. `stopped` is `idle`, not a failure — a server you have not started
 * has not gone wrong.
 */
function stateKind(state: McpServer['state']): RowKind {
  if (state === 'ready') return 'ok';
  if (state === 'error') return 'fail';
  if (state === 'starting') return 'warn';
  return 'idle';
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
    <Chip
      kind={registry ? 'warn' : 'idle'}
      title={
        registry
          ? 'Third-party code installed from the MCP registry.'
          : 'A project you scaffolded in the Author section.'
      }
    >
      {registry ? 'third-party' : 'yours'}
    </Chip>
  );
}

function ServerCard({
  server,
  index,
  onChanged,
}: {
  server: McpServer;
  index: number;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      // Connecting a stdio server runs someone's package and can fail in a dozen
      // ways; swallowing that left the button looking inert.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    const ok = await dialogs.confirm({
      title: `Remove “${server.name || server.id}”?`,
      message:
        'Forgets the configuration and any stored credential for it. The installed package, if there is one, stays on disk.',
      confirmLabel: 'Remove',
      danger: true,
    });
    if (ok) await act(() => deleteServer(server.id));
  };

  const unavailable = server.transport === 'stdio' && !server.target.available;
  const command =
    server.transport === 'stdio'
      ? (server.target.argv ?? [server.command, ...server.args]).join(' ')
      : `${server.transport.toUpperCase()} ${server.url}${server.hasToken ? ' · authenticated' : ''}`;

  return (
    <DataRow
      kind={stateKind(server.state)}
      index={index}
      title={server.name || server.id}
      meta={[
        server.group,
        server.state,
        ...(server.state === 'ready'
          ? [
              `${server.tools.length} tools`,
              `${server.prompts.length} prompts`,
              `${server.resources.length} resources`,
            ]
          : []),
      ]}
      metaTone={server.state === 'error' ? 'fail' : undefined}
      badge={<OriginChip origin={server.origin} />}
      actions={
        <>
          <Button size="sm" disabled={busy} onClick={() => act(() => connectServer(server.id))}>
            {server.state === 'ready' ? 'Reconnect' : 'Connect'}
          </Button>
          {server.state === 'ready' && (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => act(() => disconnectServer(server.id))}
            >
              Disconnect
            </Button>
          )}
          <Button intent="danger" size="sm" disabled={busy} onClick={remove}>
            Remove
          </Button>
        </>
      }
      footnotes={
        <>
          {unavailable && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>
              <code>{server.command}</code> is not on PATH on this machine.
            </div>
          )}
          {server.error && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>{server.error}</div>
          )}
          {error && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>{error}</div>
          )}
          {/* A server that starts but has no credential fails inside the *server*,
              whose error text is rarely legible. Saying it here turns that into a
              fixable state rather than a mystery. */}
          {server.missingSecretEnv.length > 0 && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--warn)' }}>
              Needs a value for {server.missingSecretEnv.join(', ')}.
            </div>
          )}
          {/* The wire is available whenever anything has been recorded — including
              for a server that never reached `ready`, which is exactly when it is
              worth reading. */}
          <div style={{ marginTop: 'var(--space-3)' }}>
            <Button intent="ghost" size="sm" onClick={() => setOpen((v) => !v)}>
              {open ? 'Hide inspector' : 'Inspect'}
            </Button>
          </div>
          {open && <ServerInspector server={server} />}
        </>
      }
    >
      <code style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-dim)' }}>{command}</code>
    </DataRow>
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

  // The other direction — this node serving an external agent. It shares this pane
  // rather than getting one of its own for the reason the other three do: they are
  // the same object (an MCP connection) seen from four positions, and a separate
  // pane would mean a fourth opener for something you reach twice a year.
  if (section === 'export') {
    return <ExportSection />;
  }

  const ready = servers.filter((s) => s.state === 'ready').length;
  const toolCount = servers.reduce((n, s) => n + s.tools.length, 0);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <PaneHeader
        title="MCP servers"
        meta={[`${ready}/${servers.length} connected`, `${toolCount} tools`]}
      />
      <div style={{ padding: 'var(--space-5)', overflow: 'auto', flex: 1 }}>
        <div
          style={{
            fontSize: 'var(--fs-meta)',
            color: 'var(--text-dim)',
            marginBottom: 'var(--space-5)',
            lineHeight: 1.5,
          }}
        >
          A connected server becomes a tool <em>group</em> the agent can load
          (<code>mcp-&lt;id&gt;</code>) — which also means a skill can name it in its
          allowed-tools and pull the whole server into a turn.
        </div>

        {error && (
          <div
            role="alert"
            style={{
              color: 'var(--danger)',
              fontSize: 'var(--fs-body)',
              marginBottom: 'var(--space-4)',
            }}
          >
            {error}
          </div>
        )}

        {loading ? (
          <div style={{ color: 'var(--text-dim)', fontSize: 'var(--fs-body)' }}>Loading…</div>
        ) : servers.length === 0 ? (
          <EmptyState title="No servers">
            Browse the registry in <strong>Discover</strong> to find one, write your own in{' '}
            <strong>Author</strong>, or add a command or URL directly below.
          </EmptyState>
        ) : (
          <DataList label="MCP servers">
            {servers.map((s, i) => (
              <ServerCard key={s.id} server={s} index={i} onChanged={refresh} />
            ))}
          </DataList>
        )}

        <AddServerForm onAdded={refresh} />
      </div>
    </div>
  );
}
