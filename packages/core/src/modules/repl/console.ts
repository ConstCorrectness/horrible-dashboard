/**
 * One REPL console — kernel connection, transcript and input history — owned by
 * the **pane**, not by the mounted component.
 *
 * `ReplPane` used to start a kernel in a mount effect and close it in the cleanup.
 * A pane unmounts on every tab switch and every desktop switch, so looking at
 * another tab killed the interpreter: every variable, import and loaded model was
 * gone when you came back, behind a transcript that had also reset. The same bug
 * `TerminalPane` had with its PTY, with the same fix — see `layout/pane-lifetime`.
 */
import { ReplSession, type CellResult } from './client';

export type EntryKind = 'in' | 'out' | 'err' | 'result' | 'system';

export interface Entry {
  kind: EntryKind;
  text: string;
}

export interface ConsoleState {
  entries: Entry[];
  busy: boolean;
}

/** Transcript cap: a console left running for a day must not grow without bound. */
const MAX_ENTRIES = 2000;

export class ReplConsole {
  readonly session: ReplSession;
  /** Submitted inputs, oldest first, for Up/Down recall. Survives remounts. */
  readonly history: string[] = [];
  private state: ConsoleState = { entries: [], busy: false };
  private listeners = new Set<() => void>();

  constructor(id: string, makeSession: typeof createSession = createSession) {
    this.session = makeSession(id, {
      onStarted: (banner) => this.append('system', banner),
      onStdout: (data) => this.append('out', data),
      onStderr: (data) => this.append('err', data),
      onResult: (result: CellResult) => {
        if (result.error) this.append('err', result.error);
        else if (result.repr != null) this.append('result', result.repr);
        this.set({ busy: false });
      },
    });
    this.session.start();
  }

  getState = (): ConsoleState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Append text, merging consecutive same-stream chunks into one block. */
  append(kind: EntryKind, text: string): void {
    if (!text) return;
    const prev = this.state.entries;
    const last = prev[prev.length - 1];
    const entries =
      last && (kind === 'out' || kind === 'err') && last.kind === kind
        ? [...prev.slice(0, -1), { kind, text: last.text + text }]
        : [...prev, { kind, text }];
    this.set({ entries: entries.length > MAX_ENTRIES ? entries.slice(-MAX_ENTRIES) : entries });
  }

  exec(code: string): void {
    this.append('in', code.trim());
    this.history.push(code);
    this.set({ busy: true });
    this.session.exec(code);
  }

  /** The pane closed: stop the kernel. */
  dispose(): void {
    this.session.dispose();
    this.listeners.clear();
  }

  private set(next: Partial<ConsoleState>): void {
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener();
  }
}

function createSession(id: string, cb: ConstructorParameters<typeof ReplSession>[1]): ReplSession {
  return new ReplSession(id, cb);
}
