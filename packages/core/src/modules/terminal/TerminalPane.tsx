/**
 * One xterm.js terminal pane, bound to a backend PTY session by the pane instance
 * id. Self-starts on mount, streams I/O over the `terminal` channel, fits to the
 * pane, and kills its PTY on unmount. A `initialCommand` pane param (set by
 * `terminal.runCommand`) is typed in once the shell is ready.
 * See docs/modules/terminal.md.
 */
import { useContext, useEffect, useRef } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';

import { PaneInstanceContext, useAgentContext } from '../../agent-context';
import { usePaneParams } from '../../panes';
import { useSetting } from '../../settings';
import { TerminalSession } from './client';
import { registerTerminal, setActiveTerminal, unregisterTerminal } from './store';

/** Resolve the `terminal.fontFamily` choice to a CSS font stack, always keeping a
 * generic monospace fallback so an uninstalled font degrades gracefully. */
function fontStack(choice: string | undefined): string {
  return !choice || choice === 'Monospace'
    ? 'var(--font-mono)'
    : `'${choice}', ui-monospace, monospace`;
}

/** Recent scrollback of an xterm terminal as plain text. */
function scrollback(term: Terminal, maxLines = 200): string {
  const buf = term.buffer.active;
  const out: string[] = [];
  const start = Math.max(0, buf.length - maxLines);
  for (let i = start; i < buf.length; i++) {
    out.push(buf.getLine(i)?.translateToString(true) ?? '');
  }
  return out.join('\n').replace(/\n+$/, '');
}

export function TerminalPane() {
  const ctxId = useContext(PaneInstanceContext);
  const params = usePaneParams();
  const fontFamily = useSetting<string>('terminal.fontFamily');
  const fontSize = useSetting<number>('terminal.fontSize') ?? 13;
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const sessionRef = useRef<TerminalSession | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  // Read path: the agent pulls this terminal's id + recent output on demand.
  useAgentContext(() => ({
    id: sessionIdRef.current,
    output: termRef.current ? scrollback(termRef.current) : '',
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    // A PTY session id unique to this effect run. Generated here (not in a ref):
    // a ref persists across a remount (e.g. StrictMode's mount→unmount→mount), so
    // the killed run's `exit` would bleed into the new session. The store maps
    // pane → live session for the commands.
    const id = `${ctxId ?? 'terminal.instance'}:${Math.random().toString(36).slice(2, 9)}`;

    const term = new Terminal({
      // Initial font from settings; a separate effect applies live changes.
      fontSize,
      fontFamily: fontStack(fontFamily),
      cursorBlink: true,
      theme: { background: '#0b0b12' },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;
    sessionIdRef.current = id;

    const session = new TerminalSession(
      id,
      (data) => term.write(data),
      () => term.write('\r\n\x1b[90m[process exited]\x1b[0m\r\n'),
      (message) => term.write(`\r\n\x1b[31m[terminal error] ${message}\x1b[0m\r\n`),
    );
    sessionRef.current = session;
    const cwd = typeof params.cwd === 'string' ? params.cwd : undefined;
    session.start(term.cols, term.rows, cwd);
    const onData = term.onData((d) => session.input(d));

    const initial = typeof params.initialCommand === 'string' ? params.initialCommand : null;
    // Let the shell print its first prompt before typing the command — and clear
    // the timer on unmount so a remount (StrictMode) doesn't write to the killed PTY.
    const initialTimer = initial ? setTimeout(() => session.input(`${initial}\r`), 250) : undefined;

    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
      session.resize(term.cols, term.rows);
    });
    resizeObserver.observe(host);

    registerTerminal({
      id,
      clear: () => term.clear(),
      focus: () => term.focus(),
      write: (data) => session.input(data),
      read: () => scrollback(term),
    });
    term.textarea?.addEventListener('focus', () => setActiveTerminal(id));

    return () => {
      if (initialTimer) clearTimeout(initialTimer);
      resizeObserver.disconnect();
      onData.dispose();
      session.kill();
      session.dispose();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      sessionRef.current = null;
      sessionIdRef.current = null;
      unregisterTerminal(id);
    };
    // Mount once per pane; ctxId/params are read once at start by design.
  }, []);

  // Apply font changes to the live terminal without remounting: update xterm's
  // options, refit (cell size changed), and tell the PTY the new rows/cols.
  useEffect(() => {
    const term = termRef.current;
    const fit = fitRef.current;
    if (!term || !fit) return;
    term.options.fontSize = fontSize;
    term.options.fontFamily = fontStack(fontFamily);
    fit.fit();
    sessionRef.current?.resize(term.cols, term.rows);
  }, [fontSize, fontFamily]);

  return <div className="terminal-pane" ref={hostRef} />;
}
