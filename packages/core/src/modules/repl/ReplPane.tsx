/**
 * One Python REPL console, bound to a backend kernel by the pane instance id. A
 * cell-based interpreter (not a PTY): type Python, Enter runs it, Shift+Enter
 * inserts a newline. stdout/stderr stream in; a trailing expression echoes its
 * repr. The injected `dash` SDK drives the dashboard. See docs/modules/repl.md.
 */
import { useContext, useEffect, useMemo, useRef, useState } from 'react';

import { PaneInstanceContext, useAgentContext } from '../../agent-context';
import { ReplSession, type CellResult } from './client';

type EntryKind = 'in' | 'out' | 'err' | 'result' | 'system';
interface Entry {
  kind: EntryKind;
  text: string;
}

const COLORS: Record<EntryKind, string> = {
  in: 'var(--accent, #7aa2f7)',
  out: 'inherit',
  err: 'var(--danger, #f7768e)',
  result: 'var(--success, #9ece6a)',
  system: 'var(--muted, #888)',
};

export function ReplPane() {
  const ctxId = useContext(PaneInstanceContext);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const sessionRef = useRef<ReplSession | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const history = useRef<string[]>([]);
  const histPos = useRef<number>(-1);

  // Append text, merging consecutive same-stream chunks into one block.
  const append = (kind: EntryKind, text: string): void => {
    if (!text) return;
    setEntries((prev) => {
      const last = prev[prev.length - 1];
      if (last && (kind === 'out' || kind === 'err') && last.kind === kind) {
        return [...prev.slice(0, -1), { kind, text: last.text + text }];
      }
      return [...prev, { kind, text }];
    });
  };

  // Read path: the agent can read this REPL's recent transcript on demand.
  useAgentContext(() => ({
    transcript: entries
      .slice(-40)
      .map((e) => `${e.kind}: ${e.text}`)
      .join('\n'),
  }));

  useEffect(() => {
    const id = `${ctxId ?? 'repl.console'}:${Math.random().toString(36).slice(2, 9)}`;
    const session = new ReplSession(id, {
      onStarted: (banner) => append('system', banner),
      onStdout: (data) => append('out', data),
      onStderr: (data) => append('err', data),
      onResult: (result: CellResult) => {
        if (result.error) append('err', result.error);
        else if (result.repr != null) append('result', result.repr);
        setBusy(false);
      },
    });
    sessionRef.current = session;
    session.start();
    return () => {
      session.dispose();
      sessionRef.current = null;
    };
    // Mount once per pane; ctxId is read once at start by design.
  }, []);

  // Keep the newest output in view.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [entries]);

  const submit = (): void => {
    const code = input.trim();
    if (!code || busy) return;
    append('in', code);
    history.current.push(input);
    histPos.current = history.current.length;
    setInput('');
    setBusy(true);
    sessionRef.current?.exec(input);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    } else if (e.key === 'ArrowUp' && !input.includes('\n')) {
      const h = history.current;
      if (h.length && histPos.current > 0) {
        e.preventDefault();
        histPos.current -= 1;
        setInput(h[histPos.current]);
      }
    } else if (e.key === 'ArrowDown' && !input.includes('\n')) {
      const h = history.current;
      if (histPos.current < h.length - 1) {
        e.preventDefault();
        histPos.current += 1;
        setInput(h[histPos.current]);
      } else {
        histPos.current = h.length;
        setInput('');
      }
    }
  };

  const containerStyle = useMemo<React.CSSProperties>(
    () => ({
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      fontFamily: 'var(--mono, ui-monospace, monospace)',
      fontSize: 13,
    }),
    [],
  );

  return (
    <div className="repl-pane" style={containerStyle}>
      <div
        className="repl-log"
        ref={logRef}
        style={{ flex: 1, overflowY: 'auto', padding: '8px 10px', whiteSpace: 'pre-wrap' }}
      >
        {entries.map((e, i) => (
          <div key={i} style={{ color: COLORS[e.kind] }}>
            {e.kind === 'in' ? `>>> ${e.text}` : e.text}
          </div>
        ))}
      </div>
      <div
        className="repl-input"
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          borderTop: '1px solid var(--border, #2a2a3a)',
        }}
      >
        <span style={{ padding: '6px 6px 6px 10px', color: COLORS.in }}>
          {busy ? '...' : '>>>'}
        </span>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          spellCheck={false}
          disabled={busy}
          placeholder="Python — Enter runs, Shift+Enter for a newline. Try: dash.panes.available()"
          style={{
            flex: 1,
            resize: 'none',
            border: 'none',
            outline: 'none',
            background: 'transparent',
            color: 'inherit',
            font: 'inherit',
            padding: '6px 10px 6px 4px',
          }}
        />
      </div>
    </div>
  );
}
