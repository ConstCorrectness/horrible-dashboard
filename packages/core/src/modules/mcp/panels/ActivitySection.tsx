/**
 * What each MCP server has actually been doing: volume, latency, failures, and the
 * cost of the runs that leaned on it.
 *
 * Read from `mcp_calls`, a durable per-call summary written by `McpSession.call_tool`
 * — the one chokepoint every invocation passes, so a server exercised only from the
 * Servers inspector still shows its traffic here.
 *
 * Three presentation rules, each there because the alternative misleads:
 *
 * - **Tool errors and transport errors are separate columns.** Both reach the model as
 *   an `error` key. One means the call or the tool is wrong, the other that the
 *   connection is — a single error rate answers neither question.
 * - **Latency is nearest-rank.** Every p95 shown is a duration some call really had.
 * - **The dollar figure is for runs that used the server, never "this server cost".**
 *   A turn's tokens are spent reading every tool result together and cannot be
 *   apportioned per tool; the column says which question it answers and how many of
 *   the runs were priced at all.
 *
 * What a server costs *before* it is called — its schemas in the context window — is
 * the inspector's job (`/servers/{id}/cost`), because that needs a live session and a
 * tokenizer and this view needs neither.
 */
import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { mcpActivity, type McpActivityStats, type McpServerActivity } from '../api';

const WINDOWS = [
  { days: 1, label: '24h' },
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
] as const;

const mono = {
  fontFamily: 'var(--font-mono)',
  fontSize: 'var(--fs-meta)',
  color: 'var(--text-secondary)',
} as const;

const label = {
  fontSize: 'var(--fs-label)',
  fontWeight: 700,
  letterSpacing: 'var(--tracking-display)',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)',
} as const;

function msText(value: number | null): string {
  if (value == null) return '—';
  return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`;
}

export function pctText(value: number | null): string {
  if (value == null) return '—';
  if (value === 0) return '0%';
  return value < 0.001 ? '<0.1%' : `${(value * 100).toFixed(1)}%`;
}

function bytesText(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function ago(seconds: number | null): string {
  if (seconds == null) return '—';
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

/** The row-level numbers, shared by a server's headline and each of its tools. */
function Stats({ stats }: { stats: McpActivityStats }) {
  return (
    <>
      <span style={mono}>
        {stats.calls.toLocaleString()} {stats.calls === 1 ? 'call' : 'calls'}
      </span>
      <span
        style={mono}
        title="Nearest-rank percentiles: each is a duration some call actually took"
      >
        p50 {msText(stats.p50_ms)} · p95 {msText(stats.p95_ms)} · p99 {msText(stats.p99_ms)}
      </span>
      <span
        style={{ ...mono, color: stats.tool_errors ? 'var(--warning)' : mono.color }}
        title="The server answered and reported the call failed"
      >
        tool err {pctText(stats.tool_error_rate)}
      </span>
      <span
        style={{ ...mono, color: stats.transport_errors ? 'var(--danger)' : mono.color }}
        title="The server never answered usefully: not connected, timed out, or the session failed"
      >
        transport err {pctText(stats.transport_error_rate)}
      </span>
      <span style={mono}>
        {bytesText(stats.request_bytes)} out · {bytesText(stats.response_bytes)} in
      </span>
    </>
  );
}

export function runCost(server: McpServerActivity): string {
  const { runs_seen, runs_priced, cost_usd_of_runs_using_server: cost } = server.runs;
  if (!runs_seen) return 'no recorded agent runs used it';
  if (cost == null) return `${runs_seen} agent ${runs_seen === 1 ? 'run' : 'runs'}, none priced`;
  const money = cost === 0 ? 'free' : cost < 0.01 ? '<$0.01' : `$${cost.toFixed(2)}`;
  return `runs that used it: ${money} across ${runs_priced} of ${runs_seen} priced`;
}

function ServerCard({ server }: { server: McpServerActivity }) {
  const [open, setOpen] = useState(false);
  const failing = server.transport_errors > 0;
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderTop: `2px solid ${failing ? 'var(--danger)' : 'var(--accent)'}`,
        borderRadius: 'var(--radius-sm)',
        background: 'var(--bg-secondary)',
        padding: 'var(--space-4)',
        marginBottom: 'var(--space-4)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <span style={{ ...label, color: 'var(--text-primary)' }}>{server.server_id}</span>
        {failing ? <Chip kind="fail">transport failures</Chip> : null}
        <span style={{ flex: 1 }} />
        <span style={mono}>last call {ago(server.last_at)}</span>
      </div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--space-2) var(--space-5)',
          marginTop: 'var(--space-3)',
        }}
      >
        <Stats stats={server} />
      </div>
      <div style={{ ...mono, marginTop: 'var(--space-2)' }}>{runCost(server)}</div>
      {server.tools.length ? (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Button intent="ghost" size="sm" onClick={() => setOpen(!open)}>
            {open
              ? 'hide tools'
              : `${server.tools.length} ${server.tools.length === 1 ? 'tool' : 'tools'}`}
          </Button>
          {open
            ? server.tools.map((tool) => (
                <div
                  key={tool.tool}
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 'var(--space-2) var(--space-5)',
                    borderTop: '1px solid var(--border)',
                    padding: 'var(--space-2) 0',
                    marginTop: 'var(--space-2)',
                  }}
                >
                  <span style={{ ...mono, color: 'var(--text-primary)', minWidth: 140 }}>
                    {tool.tool}
                  </span>
                  <Stats stats={tool} />
                </div>
              ))
            : null}
        </div>
      ) : null}
    </div>
  );
}

export function ActivitySection() {
  const [days, setDays] = useState<number>(7);
  const [servers, setServers] = useState<McpServerActivity[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await mcpActivity(days);
      setServers(res.servers);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [days]);

  useEffect(() => {
    setServers(null);
    void refresh();
  }, [refresh]);

  const total = servers?.reduce((n, s) => n + s.calls, 0) ?? 0;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <PaneHeader
        title="MCP activity"
        meta={
          servers
            ? [
                `${servers.length} ${servers.length === 1 ? 'server' : 'servers'}`,
                `${total.toLocaleString()} ${total === 1 ? 'call' : 'calls'}`,
              ]
            : []
        }
        actions={
          <>
            {WINDOWS.map((w) => (
              <Button
                key={w.days}
                intent={w.days === days ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setDays(w.days)}
              >
                {w.label}
              </Button>
            ))}
            <Button intent="ghost" size="sm" onClick={() => void refresh()}>
              Refresh
            </Button>
          </>
        }
      />
      <div style={{ padding: 'var(--space-5)', overflow: 'auto', flex: 1 }}>
        {error ? (
          <div
            role="alert"
            style={{ ...mono, color: 'var(--danger)', marginBottom: 'var(--space-4)' }}
          >
            {error}
          </div>
        ) : null}
        {servers === null && !error ? (
          <div style={mono}>Loading…</div>
        ) : servers && servers.length === 0 ? (
          <EmptyState title="No MCP calls in this window">
            Every tool call — from the agent, from the Servers inspector, from a conformance run —
            is recorded here once it happens. Only a summary is kept: no arguments and no results.
          </EmptyState>
        ) : (
          servers?.map((server) => <ServerCard key={server.server_id} server={server} />)
        )}
      </div>
    </div>
  );
}
