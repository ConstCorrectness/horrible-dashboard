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
 * **Which shell** comes from the `shell` pane param, else the `terminal.shell`
 * setting, else the platform default. It is an id from `GET /api/terminal/shells`,
 * never a path — the wire refuses paths, or the terminal channel would be an
 * arbitrary-exec route. Switching it is a **respawn**, not a swap: a PTY is bound to
 * the process it started, so the old one is killed and a new pane session created.
 *
 * A `initialCommand` pane param (set by `terminal.runCommand`) is typed in once the
 * shell is ready — once per *pane* now, not once per mount, which used to retype it
 * on every return. See docs/modules/terminal.mdx.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import type { ITheme } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';

import { useAgentContext } from '../../agent-context';
import { closePaneSession, paneSession } from '../../layout/pane-lifetime';
import { usePaneSessionKey } from '../../layout/use-pane-session';
import { usePaneParams } from '../../panes';
import { Button } from '../../Primitives';
import { useSetting } from '../../settings';
import { readThemeTokens, useThemeId } from '../../theme';
import { TerminalSession } from './client';
import { isLaunchable, shellLabel, useShells } from './shells';
import { registerTerminal, setActiveTerminal, unregisterTerminal } from './store';

/** Resolve the `terminal.fontFamily` choice to a CSS font stack, always keeping a
 * generic monospace fallback so an uninstalled font degrades gracefully. */
function fontStack(choice: string | undefined): string {
  return !choice || choice === 'Monospace'
    ? 'var(--font-mono)'
    : `'${choice}', ui-monospace, monospace`;
}

/**
 * xterm's palette, from the live theme.
 *
 * It used to be `{ background: '#0b0b12' }` — a hardcoded near-black, so under the
 * light themes the terminal was the one pane that stayed dark, which reads as a
 * rendering bug rather than a choice. Only the surface colours are mapped: the 16
 * ANSI slots are a program's own vocabulary (a prompt that paints itself green means
 * green), and remapping those would recolour output the user wrote.
 */
function themePalette(): ITheme {
  const t = readThemeTokens(['bg-primary', 'text-primary', 'accent', 'bg-secondary'] as const);
  return {
    background: t['bg-primary'] || undefined,
    foreground: t['text-primary'] || undefined,
    cursor: t.accent || undefined,
    cursorAccent: t['bg-primary'] || undefined,
    selectionBackground: t['bg-secondary'] || undefined,
  };
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
  /** What the backend said it actually spawned. Null until `started` arrives. */
  shell: string | null;
}

function createTerminal(
  host: HTMLElement,
  key: string,
  params: Record<string, unknown>,
  fontSize: number,
  fontFamily: string | undefined,
  shell: string | undefined,
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
    theme: themePalette(),
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(el);
  fit.fit();

  const live: LiveTerminal = {
    id,
    term,
    fit,
    el,
    shell: null,
    session: null as unknown as TerminalSession,
    onData: { dispose: () => {} },
  };

  live.session = new TerminalSession(
    id,
    (data) => term.write(data),
    () => term.write('\r\n\x1b[90m[process exited]\x1b[0m\r\n'),
    (message) => term.write(`\r\n\x1b[31m[terminal error] ${message}\x1b[0m\r\n`),
    (actual, requested) => {
      live.shell = actual;
      // The backend fell back. Say so in the terminal itself rather than only in
      // the picker: the prompt in front of the user is not the shell they chose,
      // and nothing else on screen would ever mention it.
      if (requested) {
        term.write(
          `\r\n\x1b[33m[${requested} is not available on this machine — started ${actual ?? 'the default shell'} instead]\x1b[0m\r\n`,
        );
      }
    },
  );
  const cwd = typeof params.cwd === 'string' ? params.cwd : undefined;
  live.session.start(term.cols, term.rows, cwd, shell);
  live.onData = term.onData((d) => live.session.input(d));

  // Let the shell print its first prompt before typing the command. Runs once per
  // pane, so returning to this workspace never retypes it.
  const initial = typeof params.initialCommand === 'string' ? params.initialCommand : null;
  if (initial) setTimeout(() => live.session.input(`${initial}\r`), 250);

  return live;
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
  const settingShell = useSetting<string>('terminal.shell');
  const themeId = useThemeId();
  const catalog = useShells();
  // Bumped to force a respawn. The effect below is keyed on it, so incrementing it
  // tears the old PTY down and builds a new one with the chosen shell.
  const [generation, setGeneration] = useState(0);
  const [chosen, setChosen] = useState<string | undefined>(undefined);
  const [running, setRunning] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const sessionRef = useRef<TerminalSession | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  // Pane param beats the setting: `openTerminal({ shell })` is a specific request,
  // the setting is the standing preference.
  const requested = useMemo(() => {
    if (chosen !== undefined) return chosen || undefined;
    if (typeof params.shell === 'string' && params.shell) return params.shell;
    return settingShell || undefined;
  }, [chosen, params.shell, settingShell]);

  // Read path: the agent pulls this terminal's id + recent output on demand.
  useAgentContext(() => ({
    id: sessionIdRef.current,
    output: termRef.current ? scrollback(termRef.current) : '',
    shell: running,
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !sessionKey) return;
    // Created on the pane's *first* mount and reused by every later one. The PTY,
    // the xterm instance and its element all outlive this component — the only
    // thing that kills them is the pane being closed (layout/pane-lifetime).
    const live = paneSession(
      sessionKey,
      () => createTerminal(host, sessionKey, params, fontSize, fontFamily, requested),
      destroyTerminal,
    );
    // Re-attach on a return visit. Moving the element rather than rebuilding the
    // terminal is what keeps the rendered scrollback intact.
    if (live.el.parentElement !== host) host.appendChild(live.el);
    live.fit.fit();
    live.term.refresh(0, live.term.rows - 1);
    setRunning(live.shell);

    // Coalesced on a frame. Unthrottled, a window drag sent one `resize` frame per
    // layout tick, flooding the shared socket to tell the PTY the same thing sixty
    // times a second.
    let raf = 0;
    const resizeObserver = new ResizeObserver(() => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        live.fit.fit();
        live.session.resize(live.term.cols, live.term.rows);
      });
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

    termRef.current = live.term;
    fitRef.current = live.fit;
    sessionRef.current = live.session;
    sessionIdRef.current = live.id;

    return () => {
      // Detach only. Nothing here is destructive — that is the fix.
      if (raf) cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      live.term.textarea?.removeEventListener('focus', onFocus);
      unregisterTerminal(live.id);
      if (live.el.parentElement === host) host.removeChild(live.el);
      termRef.current = null;
      fitRef.current = null;
      sessionRef.current = null;
      sessionIdRef.current = null;
    };
    // Params/fonts are read once at creation by design; fonts and theme have their
    // own effects. `generation` is the respawn signal.
  }, [sessionKey, generation]);

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

  // Repaint on a theme switch. Re-read rather than remount: the palette is the only
  // thing that changed, and rebuilding would take the shell with it.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = themePalette();
  }, [themeId]);

  function pick(id: string | null): void {
    setPicking(false);
    const next = id ?? '';
    if ((chosen ?? '') === next && running === id) return;
    // A PTY is bound to the process it started; there is no swapping the shell
    // underneath one. Kill the session and let the effect build a new one.
    setChosen(next);
    if (sessionKey) closePaneSession(sessionKey);
    setGeneration((g) => g + 1);
  }

  const launchable = catalog.shells.filter(isLaunchable);

  return (
    <div className="terminal-pane">
      <div className="terminal-pane__bar">
        <Button
          intent="ghost"
          size="sm"
          onClick={() => setPicking((open) => !open)}
          title="Change the shell (restarts this terminal)"
        >
          {shellLabel(catalog, running ?? requested ?? catalog.default)}
        </Button>
        {picking && (
          <div className="terminal-pane__menu" role="menu">
            <p className="terminal-pane__warn">Switching restarts this terminal.</p>
            {launchable.map((shell) => (
              <button
                key={shell.id}
                type="button"
                role="menuitem"
                className="terminal-pane__option"
                onClick={() => pick(shell.id)}
              >
                <span className="terminal-pane__option-label">{shell.label}</span>
                <span className="terminal-pane__option-path">{shell.path}</span>
                {shell.note && <span className="terminal-pane__option-note">{shell.note}</span>}
              </button>
            ))}
            {/* Entries the backend could not verify carry no path and cannot be
                launched, but they are shown so "we could not check" never renders
                as "you do not have this". */}
            {catalog.shells
              .filter((s) => !isLaunchable(s))
              .map((shell) => (
                <p key={shell.id} className="terminal-pane__option-note">
                  {shell.label}: {shell.note}
                </p>
              ))}
            {launchable.length === 0 && (
              <p className="terminal-pane__option-note">
                No shells reported. This terminal is running the platform default.
              </p>
            )}
          </div>
        )}
      </div>
      <div className="terminal-pane__surface" ref={hostRef} />
    </div>
  );
}
