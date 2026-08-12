import { useState } from 'react';

import { runConformance, type McpCheckStatus, type McpConformance, type McpServer } from '../api';

/**
 * The conformance suite's report.
 *
 * The disclaimer at the bottom is not boilerplate — it is the honest limit of what any
 * of this can establish. The suite reads the server's declarations and pokes its
 * protocol edges; it cannot tell whether a tool annotated `readOnlyHint` actually only
 * reads. Presenting a green run as "this server is safe" would be the exact failure
 * this pane exists to prevent, so the report says what it checked.
 */

const COLOR: Record<McpCheckStatus, string> = {
  pass: 'var(--ok, #3fb950)',
  warn: 'var(--warn, #d29922)',
  fail: 'var(--danger, #f85149)',
  skip: 'var(--text-dim)',
};

const GLYPH: Record<McpCheckStatus, string> = { pass: '✓', warn: '!', fail: '✕', skip: '–' };

export function ConformanceView({ server }: { server: McpServer }) {
  const [report, setReport] = useState<McpConformance | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await runConformance(server.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <button disabled={busy} onClick={() => void run()}>
        {busy ? 'Checking…' : report ? 'Run again' : 'Run suite'}
      </button>

      {error && <div style={{ color: 'var(--danger, #f85149)', marginTop: '0.3rem' }}>{error}</div>}

      {report && (
        <div style={{ marginTop: '0.4rem' }}>
          <div style={{ marginBottom: '0.35rem' }}>
            <span style={{ color: COLOR[report.status], fontWeight: 600 }}>
              {report.status.toUpperCase()}
            </span>{' '}
            <span style={{ color: 'var(--text-dim)' }}>
              {report.serverName} {report.serverVersion} · protocol {report.protocolVersion}
            </span>
          </div>
          {report.checks.map((check) => (
            <div key={check.id} style={{ marginBottom: '0.3rem' }}>
              <span style={{ color: COLOR[check.status] }}>{GLYPH[check.status]}</span>{' '}
              <strong style={{ fontWeight: 500 }}>{check.title}</strong>
              {check.detail && (
                <div
                  style={{
                    color: 'var(--text-dim)',
                    fontSize: '0.68rem',
                    marginLeft: '1rem',
                  }}
                >
                  {check.detail}
                </div>
              )}
            </div>
          ))}
          <div
            style={{
              color: 'var(--text-dim)',
              fontSize: '0.66rem',
              borderTop: '1px solid var(--border)',
              paddingTop: '0.3rem',
              marginTop: '0.4rem',
            }}
          >
            Checks declarations and protocol edges — never whether an annotation is true. A tool
            marked read-only can still write; only its source can tell you. No declared tool is
            called with valid arguments.
          </div>
        </div>
      )}
    </div>
  );
}
