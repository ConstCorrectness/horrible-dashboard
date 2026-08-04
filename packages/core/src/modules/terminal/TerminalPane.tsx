/**
 * One xterm.js terminal pane, bound to a backend PTY session by the pane instance
 * id. Streams I/O over the `terminal` channel and fits to the pane.
 *
 * **The shell outlives the component.** A pane unmounts whenever its tab is not
 * the active one and whenever you look at another workspace, and this used to kill
 * the PTY on unmount — so switching workspace tabs silently destroyed your shell,
 * its cwd and anything running in it, then handed back a fresh one that looked the
 * same. The terminal now lives in a `paneSession` (see layout/pane-lifetime) and is
 * killed only when the pane is genuinely closed. Its xterm element is kept and
 * re-attached, so scrollback and the live process survive a round trip untouched.
 *
 * A `initialCommand` pane param (set by `terminal.runCommand`) is typed in once the
 * shell is ready — once per *pane* now, not once per mount, which used to retype it
 * on every return. See docs/modules/terminal.md.
 */
import { useEffect, useRef } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';

import { useAgentContext } from '../../agent-context';
import { paneSession } from '../../layout/pane-lifetime';
import { usePaneSessionKey } from '../../layout/use-pane-session';
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

/** Everything one terminal pane owns, kept alive across unmounts. */
interface LiveTerminal {
  id: string;
  term: Terminal;
  fit: FitAddon;
  session: TerminalSession;
  /** xterm's own host element, moved between mounts so canvases survive intact. */
  el: HTMLDivElement;
  onData: { dispose: () => void };
}

function createTerminal(
  host: HTMLElement,
  key: string,
  params: Record<string, unknown>,
  fontSize: number,
  fontFamily: string | undefined,
): LiveTerminal {
  // Derived from the pane session key, so it is stable across remounts — the PTY
  // is addressed by this id and a fresh one per mount would orphan the old shell.
  const id = `${key}:${Math.random().toString(36).slice(2, 9)}`;
  const el = document.createElement('div');
  el.className = 'terminal-surface';
  // xterm measures on open(), so the element has to be in the document first.
  host.appendChild(el);

  const term = new Terminal({
    fontSize,
    fontFamily: fontStack(fontFamily),
    cursorBlink: true,
    theme: { background: '#0b0b12' },
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(el);
  fit.fit();

  const session = new TerminalSession(
    id,
    (data) => term.write(data),
    () => term.write('\r\n\x1b[90m[process exited]\x1b[0m\r\n'),
    (message) => term.write(`\r\n\x1b[31m[terminal error] ${message}\x1b[0m\r\n`),
  );
  const cwd = typeof params.cwd === 'string' ? params.cwd : undefined;
  session.start(term.cols, term.rows, cwd);
  const onData = term.onData((d) => session.input(d));

  // Let the shell print its first prompt before typing the command. Runs once per
  // pane, so returning to this workspace never retypes it.
  const initial = typeof params.initialCommand === 'string' ? params.initialCommand : null;
  if (initial) setTimeout(() => session.input(`${initial}\r`), 250);

  return { id, term, fit, session, el, onData };
}

/** Only on a real close: this is what kills the shell. */
function destroyTerminal(live: LiveTerminal): void {
  live.onData.dispose();
  live.session.kill();
  live.session.dispose();
  live.term.dispose();
  live.el.remove();
}

export function TerminalPane() {
  const sessionKey = usePaneSessionKey();
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
    if (!host || !sessionKey) return;
    // Created on the pane's *first* mount and reused by every later one. The PTY,
    // the xterm instance and its element all outlive this component — the only
    // thing that kills them is the pane being closed (layout/pane-lifetime).
    const live = paneSession(
      sessionKey,
      () => createTerminal(host, sessionKey, params, fontSize, fontFamily),
      destroyTerminal,
    );
    // Re-attach on a return visit. Moving the element rather than rebuilding the
    // terminal is what keeps the rendered scrollback intact.
    if (live.el.parentElement !== host) host.appendChild(live.el);
    live.fit.fit();
    live.term.refresh(0, live.term.rows - 1);

    termRef.current = live.term;
    fitRef.current = live.fit;
    sessionRef.current = live.session;
    sessionIdRef.current = live.id;

    const resizeObserver = new ResizeObserver(() => {
      live.fit.fit();
      live.session.resize(live.term.cols, live.term.rows);
    });
    resizeObserver.observe(host);

    registerTerminal({
      id: live.id,
      clear: () => live.term.clear(),
      focus: () => live.term.focus(),
      write: (data) => live.session.input(data),
      read: () => scrollback(live.term),
    });
    const onFocus = () => setActiveTerminal(live.id);
    live.term.textarea?.addEventListener('focus', onFocus);

    return () => {
      // Detach only. Nothing here is destructive — that is the fix.
      resizeObserver.disconnect();
      live.term.textarea?.removeEventListener('focus', onFocus);
      unregisterTerminal(live.id);
      if (live.el.parentElement === host) host.removeChild(live.el);
      termRef.current = null;
      fitRef.current = null;
      sessionRef.current = null;
      sessionIdRef.current = null;
    };
    // Params/fonts are read once at creation by design; fonts have their own effect.
  }, [sessionKey]);

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
