/**
 * One Python REPL console, bound to a backend kernel by the pane instance id. A
 * cell-based interpreter (not a PTY): type Python, Enter runs it, Shift+Enter
 * inserts a newline. stdout/stderr stream in; a trailing expression echoes its
 * repr. The injected `dash` SDK drives the dashboard. See docs/modules/repl.md.
 */
import { useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { PaneInstanceContext, useAgentContext } from '../../agent-context';
import { usePaneSession } from '../../layout/use-pane-session';
import { ReplConsole, type EntryKind } from './console';

function newConsole(ctxId: string | null): ReplConsole {
  return new ReplConsole(`${ctxId ?? 'repl.console'}:${Math.random().toString(36).slice(2, 9)}`);
}

const disposeConsole = (c: ReplConsole): void => c.dispose();

/**
 * This pane's console: created once per pane and stopped only when the pane
 * closes (see `console.ts`). Outside a pane there is no close event, so it falls
 * back to the old per-mount lifetime rather than leaking a kernel forever.
 */
function useReplConsole(ctxId: string | null): ReplConsole {
  const paneScoped = usePaneSession(() => newConsole(ctxId), disposeConsole);
  const [local] = useState<ReplConsole | null>(() => (paneScoped ? null : newConsole(ctxId)));
  useEffect(() => (local ? () => local.dispose() : undefined), [local]);
  return (paneScoped ?? local)!;
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
  const repl = useReplConsole(ctxId);
  const { entries, busy } = useSyncExternalStore(repl.subscribe, repl.getState);
  const [input, setInput] = useState('');
  const logRef = useRef<HTMLDivElement>(null);
  const history = repl.history;
  const histPos = useRef<number>(history.length);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wasBusyRef = useRef(false);

  useEffect(() => {
    if (wasBusyRef.current && !busy) {
      inputRef.current?.focus();
    }
    wasBusyRef.current = busy;
  }, [busy]);

  // Read path: the agent can read this REPL's recent transcript on demand.
  useAgentContext(() => ({
    transcript: entries
      .slice(-40)
      .map((e) => `${e.kind}: ${e.text}`)
      .join('\n'),
  }));

  // Keep the newest output in view.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [entries]);

  // Grow the input box to fit its content (or its placeholder, when empty) so a
  // narrow pane that wraps the placeholder onto multiple lines doesn't clip it
  // against a box still sized for one row.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    const resize = (): void => {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, [input]);

  const submit = (): void => {
    const code = input.trim();
    if (!code || busy) return;
    repl.exec(input);
    histPos.current = history.length;
    setInput('');
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      // Smart REPL enter behavior:
      // If code is incomplete (ends with colon, has open brackets, or is in an indented block),
      // insert a newline with auto-indentation. Otherwise, execute it.
      const lines = input.split('\n');
      const lastLine = lines[lines.length - 1];
      const trimmedLastLine = lastLine.trim();

      const countChar = (str: string, char: string) => {
        let count = 0;
        for (let i = 0; i < str.length; i++) {
          if (str[i] === char) count++;
        }
        return count;
      };

      const openParentheses = countChar(input, '(') - countChar(input, ')');
      const openBrackets = countChar(input, '[') - countChar(input, ']');
      const openBraces = countChar(input, '{') - countChar(input, '}');
      const hasOpenBrackets = openParentheses > 0 || openBrackets > 0 || openBraces > 0;

      const endsWithColon = trimmedLastLine.endsWith(':');
      const isIndentedBlock = lines.length > 1 || lastLine.startsWith(' ');
      const lastLineNotEmpty = trimmedLastLine !== '';

      const shouldSubmit =
        !endsWithColon && !hasOpenBrackets && (!isIndentedBlock || !lastLineNotEmpty);

      if (!shouldSubmit) {
        e.preventDefault();
        const match = lastLine.match(/^(\s*)/);
        const currentIndent = match ? match[1] : '';
        const extraIndent = endsWithColon ? '    ' : '';
        const newIndent = currentIndent + extraIndent;

        const start = e.currentTarget.selectionStart;
        const end = e.currentTarget.selectionEnd;
        const val = e.currentTarget.value;
        const newValue = val.substring(0, start) + '\n' + newIndent + val.substring(end);
        setInput(newValue);

        const newCursorPos = start + 1 + newIndent.length;
        setTimeout(() => {
          if (e.currentTarget) {
            e.currentTarget.selectionStart = newCursorPos;
            e.currentTarget.selectionEnd = newCursorPos;
          }
        }, 0);
        return;
      }

      e.preventDefault();
      submit();
    } else if (e.key === 'ArrowUp' && !input.includes('\n')) {
      const h = history;
      if (h.length && histPos.current > 0) {
        e.preventDefault();
        histPos.current -= 1;
        setInput(h[histPos.current]);
      }
    } else if (e.key === 'ArrowDown' && !input.includes('\n')) {
      const h = history;
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

  const handleContainerClick = (): void => {
    if (busy) return;
    const selection = window.getSelection();
    if (selection && selection.toString()) {
      return;
    }
    inputRef.current?.focus();
  };

  const containerStyle = useMemo<React.CSSProperties>(
    () => ({
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      fontFamily: 'var(--font-mono)',
      fontSize: 13,
    }),
    [],
  );

  return (
    <div className="repl-pane" style={containerStyle} onClick={handleContainerClick}>
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
          ref={inputRef}
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
            overflow: 'hidden',
            border: 'none',
            outline: 'none',
            background: 'transparent',
            color: 'inherit',
            font: 'inherit',
            padding: '6px 10px 6px 4px',
            lineHeight: 1.4,
          }}
        />
      </div>
    </div>
  );
}
