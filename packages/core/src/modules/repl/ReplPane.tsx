/**
 * One Python REPL console, bound to a backend kernel by the pane instance id. A
 * cell-based interpreter with full IDE intellisense (CodeMirror 6, Python syntax
 * highlighting, live Jedi kernel autocompletion and inspection, doc tooltips,
 * and inline autosuggest). See docs/modules/repl.md.
 */
import { useContext, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { python } from '@codemirror/lang-python';
import { EditorState, Prec } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, keymap, placeholder } from '@codemirror/view';

import { PaneInstanceContext, useAgentContext } from '../../agent-context';
import { docsHover, docsKeymap } from '../../docs/cm-docs';
import { usePaneSession } from '../../layout/use-pane-session';
import { autosuggest } from '../editor/autosuggest';
import { buildCompletion, completionKeymap } from '../editor/completion';
import { dbGhostFetch } from '../editor/symbolCompletion';
import { ReplConsole, type EntryKind } from './console';
import { replCompletionSource } from './replCompletion';

function newConsole(ctxId: string | null): ReplConsole {
  return new ReplConsole(`${ctxId ?? 'repl.console'}:${Math.random().toString(36).slice(2, 9)}`);
}

const disposeConsole = (c: ReplConsole): void => c.dispose();

function useReplConsole(ctxId: string | null): ReplConsole {
  const paneScoped = usePaneSession(() => newConsole(ctxId), disposeConsole);
  const localRef = useRef<ReplConsole | null>(null);
  if (!paneScoped && !localRef.current) {
    localRef.current = newConsole(ctxId);
  }
  useEffect(() => () => { localRef.current?.dispose(); }, []);
  return paneScoped ?? localRef.current!;
}

const COLORS: Record<EntryKind, string> = {
  in: 'var(--accent, #7aa2f7)',
  out: 'inherit',
  err: 'var(--danger, #f7768e)',
  result: 'var(--success, #9ece6a)',
  system: 'var(--muted, #888)',
};

function isCodeIncomplete(input: string): boolean {
  const lines = input.split('\n');
  const lastLine = lines[lines.length - 1];
  const trimmedLastLine = lastLine.trim();

  let openParentheses = 0;
  let openBrackets = 0;
  let openBraces = 0;
  for (let i = 0; i < input.length; i++) {
    const c = input[i];
    if (c === '(') openParentheses++;
    else if (c === ')') openParentheses--;
    else if (c === '[') openBrackets++;
    else if (c === ']') openBrackets--;
    else if (c === '{') openBraces++;
    else if (c === '}') openBraces--;
  }
  const hasOpenBrackets = openParentheses > 0 || openBrackets > 0 || openBraces > 0;
  const endsWithColon = trimmedLastLine.endsWith(':');
  const isIndentedBlock = lines.length > 1 || lastLine.startsWith(' ');
  const lastLineNotEmpty = trimmedLastLine !== '';

  return endsWithColon || hasOpenBrackets || (isIndentedBlock && lastLineNotEmpty);
}

function getIndent(input: string): string {
  const lines = input.split('\n');
  const lastLine = lines[lines.length - 1];
  const match = lastLine.match(/^(\s*)/);
  const currentIndent = match ? match[1] : '';
  const extraIndent = lastLine.trim().endsWith(':') ? '    ' : '';
  return currentIndent + extraIndent;
}

export function ReplPane() {
  const ctxId = useContext(PaneInstanceContext);
  const repl = useReplConsole(ctxId);
  const { entries, busy } = useSyncExternalStore(repl.subscribe, repl.getState);
  const logRef = useRef<HTMLDivElement>(null);
  const editorHostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const cmdHistory = repl.history;
  const histPos = useRef<number>(cmdHistory.length);
  const wasBusyRef = useRef(false);

  useEffect(() => {
    if (wasBusyRef.current && !busy) {
      viewRef.current?.focus();
    }
    wasBusyRef.current = busy;
  }, [busy]);

  useAgentContext(() => ({
    transcript: entries
      .slice(-40)
      .map((e) => `${e.kind}: ${e.text}`)
      .join('\n'),
  }));

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [entries]);

  const submit = (): void => {
    const view = viewRef.current;
    if (!view || repl.getState().busy) return;
    const text = view.state.doc.toString();
    const code = text.trim();
    if (!code) return;
    repl.exec(text);
    histPos.current = cmdHistory.length;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: '' } });
  };

  useEffect(() => {
    if (!editorHostRef.current) return;

    const replKeys = Prec.highest(
      keymap.of([
        {
          key: 'Enter',
          run: (view) => {
            if (repl.getState().busy) return true;
            const text = view.state.doc.toString();
            if (isCodeIncomplete(text)) {
              const indent = getIndent(text);
              const head = view.state.selection.main.head;
              view.dispatch({
                changes: { from: head, insert: `\n${indent}` },
                selection: { anchor: head + 1 + indent.length },
              });
              return true;
            }
            submit();
            return true;
          },
        },
        {
          key: 'Shift-Enter',
          run: (view) => {
            const text = view.state.doc.toString();
            const indent = getIndent(text);
            const head = view.state.selection.main.head;
            view.dispatch({
              changes: { from: head, insert: `\n${indent}` },
              selection: { anchor: head + 1 + indent.length },
            });
            return true;
          },
        },
        {
          key: 'ArrowUp',
          run: (view) => {
            const head = view.state.selection.main.head;
            const line = view.state.doc.lineAt(head);
            if (line.number === 1 && cmdHistory.length && histPos.current > 0) {
              histPos.current -= 1;
              const prev = cmdHistory[histPos.current];
              view.dispatch({
                changes: { from: 0, to: view.state.doc.length, insert: prev },
                selection: { anchor: prev.length },
              });
              return true;
            }
            return false;
          },
        },
        {
          key: 'ArrowDown',
          run: (view) => {
            const head = view.state.selection.main.head;
            const line = view.state.doc.lineAt(head);
            if (line.number === view.state.doc.lines) {
              if (histPos.current < cmdHistory.length - 1) {
                histPos.current += 1;
                const next = cmdHistory[histPos.current];
                view.dispatch({
                  changes: { from: 0, to: view.state.doc.length, insert: next },
                  selection: { anchor: next.length },
                });
                return true;
              } else if (histPos.current === cmdHistory.length - 1) {
                histPos.current = cmdHistory.length;
                view.dispatch({
                  changes: { from: 0, to: view.state.doc.length, insert: '' },
                });
                return true;
              }
            }
            return false;
          },
        },
      ]),
    );

    const docResolver = async (req: { code?: string; cursorPos?: number }) => {
      const entry = await repl.session.inspect(req.code ?? '', req.cursorPos ?? 0);
      return entry ? [entry] : null;
    };

    const view = new EditorView({
      parent: editorHostRef.current,
      state: EditorState.create({
        doc: '',
        extensions: [
          history(),
          replKeys,
          buildCompletion({
            languageId: 'python',
            extraSources: [replCompletionSource(repl.session)],
          }),
          completionKeymap,
          autosuggest({
            fetch: (prefix, suffix) => dbGhostFetch(prefix, suffix, 'python'),
          }),
          docsHover({ resolver: docResolver }),
          docsKeymap({ resolver: docResolver, show: () => {} }),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          python(),
          oneDark,
          placeholder('Python — Enter runs, Shift+Enter for a newline. Try: dash.panes.available()'),
          EditorView.lineWrapping,
          EditorView.theme({
            '&': {
              fontSize: '13px',
              fontFamily: 'var(--font-mono, monospace)',
              backgroundColor: 'transparent',
              outline: 'none',
              flex: '1',
              maxHeight: '180px',
            },
            '.cm-scroller': {
              overflowY: 'auto',
            },
            '.cm-content': {
              padding: '6px 10px 6px 4px',
              lineHeight: 1.4,
            },
            '&.cm-focused': {
              outline: 'none',
            },
          }),
        ],
      }),
    });

    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [repl]);

  const handleContainerClick = (): void => {
    if (busy) return;
    const selection = window.getSelection();
    if (selection && selection.toString()) return;
    viewRef.current?.focus();
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
        <div ref={editorHostRef} style={{ flex: 1, minWidth: 0 }} />
      </div>
    </div>
  );
}
