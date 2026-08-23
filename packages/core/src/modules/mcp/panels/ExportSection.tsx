/**
 * The other direction: this node **as** an MCP server.
 *
 * Everything here has existed on the backend since the export landed —
 * `GET /api/mcp/export`, `POST /api/mcp/export/token`, seven read-only tools — and
 * nothing in the app called any of it. There was no way to see whether the export
 * was on, no way to reveal or rotate the bearer token, and no mention anywhere of
 * the environment variable that enables it. The one thing that *did* surface was
 * `mcp.server.exposeContent`, sitting in the generic settings page describing a
 * server the user could not see.
 *
 * It is an **interpretability surface, not a control surface**: every exported tool
 * is read-only, and a test asserts that, because a write tool here would turn a
 * bearer token into remote control of the node. The pane says so, because "MCP
 * server" reads like "remote control" to most people and the distinction is the
 * whole safety argument.
 *
 * The token is deliberately awkward to obtain — one click, and it is not in the
 * status payload — for the reason the route's own docstring gives: it grants read
 * access to this node's trajectories and telemetry, which include the user's
 * prompts and whatever they had open.
 */
import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { dialogs } from '../../../dialogs';
import { useSetting } from '../../../settings';
import { setSetting } from '../../../settings';
import { exportStatus, revealExportToken, type McpExportStatus } from '../api';

export function ExportSection() {
  const [status, setStatus] = useState<McpExportStatus | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const exposeContent = useSetting<boolean>('mcp.server.exposeContent');

  const refresh = useCallback(async () => {
    try {
      setStatus(await exportStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const reveal = async (rotate: boolean) => {
    if (rotate) {
      const ok = await dialogs.confirm({
        title: 'Rotate the bearer token?',
        // Naming the consequence, not just asking twice: the old token stops
        // working immediately and anything already using it breaks.
        message:
          'The current token stops working at once. Any Claude Desktop config, script or CI job already using it will start failing until you paste the new one in.',
        confirmLabel: 'Rotate',
        danger: true,
      });
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await revealExportToken(rotate);
      setToken(res.token);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!status) {
    return (
      <div style={{ padding: 'var(--space-5)' }}>
        {error ? (
          <div role="alert" style={{ color: 'var(--danger)', fontSize: 'var(--fs-body)' }}>
            {error}
          </div>
        ) : (
          <span style={{ color: 'var(--text-dim)', fontSize: 'var(--fs-body)' }}>Checking…</span>
        )}
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <PaneHeader
        title="This node as a server"
        meta={[status.enabled ? 'mounted' : 'off', status.mountPath]}
        actions={
          status.enabled ? (
            <Chip kind="ok" dot>
              on
            </Chip>
          ) : (
            <Chip dot>off</Chip>
          )
        }
      />

      <div
        style={{
          padding: 'var(--space-5)',
          overflow: 'auto',
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-5)',
        }}
      >
        <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-dim)', lineHeight: 1.55 }}>
          Lets an external agent — Claude Desktop, another node, a CI job — ask what this node's
          agent has been doing. Seven tools, <strong>all read-only</strong>: which agents exist,
          which turns ran, how work was delegated, and recent network I/O as metadata. Nothing here
          can open a pane, run a command, edit a file or start a turn.
        </div>

        {!status.enabled ? (
          <EmptyState title="Not mounted">
            Off by default, and it stays off until you set <code>{status.enableEnv}=1</code> in the
            environment and restart the backend. Two independent reasons: I/O events capture raw
            headers and bodies, and turn snapshots contain your prompts and whatever file you had
            open.
          </EmptyState>
        ) : (
          <>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--space-3)',
                padding: 'var(--space-5)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-raised)',
              }}
            >
              <div
                style={{
                  fontSize: 'var(--fs-label)',
                  fontWeight: 'var(--fw-bold)',
                  letterSpacing: 'var(--tracking-display)',
                  textTransform: 'uppercase',
                  color: 'var(--text-dim)',
                }}
              >
                Bearer token
              </div>
              <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-faint)' }}>
                Required on every request. Treat it like a password — it reads your prompts and your
                node's traffic metadata.
              </div>
              {token ? (
                <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'stretch' }}>
                  {/* A read-only input rather than <code>: it selects on click and
                      survives a clipboard API that is unavailable over plain http
                      on a LAN address, where `navigator.clipboard` is undefined and
                      a copy button alone would silently do nothing. */}
                  <input
                    readOnly
                    value={token}
                    onFocus={(e) => e.currentTarget.select()}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      background: 'var(--bg-inset)',
                      color: 'var(--text)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      padding: 'var(--space-2) var(--space-3)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: 'var(--fs-meta)',
                    }}
                  />
                  <Button
                    size="sm"
                    onClick={() => void navigator.clipboard?.writeText(token)}
                    disabled={!navigator.clipboard}
                    title={
                      navigator.clipboard
                        ? 'Copy to clipboard'
                        : 'Clipboard unavailable here — select the text instead'
                    }
                  >
                    Copy
                  </Button>
                </div>
              ) : (
                <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-dim)' }}>
                  {status.hasToken
                    ? 'A token exists. It is not shown until you ask for it.'
                    : 'No token yet — revealing one mints it.'}
                </div>
              )}
              <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                <Button size="sm" disabled={busy} onClick={() => void reveal(false)}>
                  {status.hasToken ? 'Reveal' : 'Create token'}
                </Button>
                {status.hasToken && (
                  <Button
                    intent="danger"
                    size="sm"
                    disabled={busy}
                    onClick={() => void reveal(true)}
                  >
                    Rotate
                  </Button>
                )}
              </div>
            </div>

            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--space-3)',
                padding: 'var(--space-5)',
                border: `1px solid ${exposeContent ? 'var(--warn)' : 'var(--border)'}`,
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-raised)',
              }}
            >
              <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                <input
                  type="checkbox"
                  checked={Boolean(exposeContent)}
                  onChange={(e) => void setSetting('mcp.server.exposeContent', e.target.checked)}
                />
                <span
                  style={{
                    fontSize: 'var(--fs-label)',
                    fontWeight: 'var(--fw-bold)',
                    letterSpacing: 'var(--tracking-display)',
                    textTransform: 'uppercase',
                    color: 'var(--text-dim)',
                  }}
                >
                  Expose turn content
                </span>
              </label>
              <div
                style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-faint)', lineHeight: 1.5 }}
              >
                Off, a turn's detail returns block <em>shape and token cost</em> but not text. On,
                the caller reads the prompts and tool results themselves. Bodies and headers of I/O
                events are never exported either way, regardless of this setting.
              </div>
              {exposeContent && (
                <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--warn)' }}>
                  Anyone holding the token can read what you typed.
                </div>
              )}
            </div>
          </>
        )}

        {error && (
          <div role="alert" style={{ color: 'var(--danger)', fontSize: 'var(--fs-body)' }}>
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
