/**
 * The minibuffer: the emacs-style strip along the very bottom of the frame,
 * below the bottom dock.
 *
 * It has two jobs, the same two emacs gives it:
 *
 * - **Status line** when idle — the workspace, the focused pane, and a transient
 *   echo area for the result of whatever just happened.
 * - **Input line** when active (`alt+x`) — slash commands (`/save`, `/find`),
 *   resolved against the same registry the command palette uses, so nothing has
 *   to be registered twice.
 *
 * It is also where **prompts** render. `dialogs.prompt()` already existed and is
 * what editor Save As calls; the minibuffer serves it inline instead of a modal,
 * so "save as, right here" works with no new API and every existing caller gets
 * it for free. `confirm`/`choice` stay modal — a destructive Save / Don't Save /
 * Cancel deserves to be interrupting, not a line you can ignore.
 *
 * This module is the state + matching (pure, unit-tested); the strip itself is
 * packages/ui/src/layout/Minibuffer.tsx. See docs/architecture/windowing.mdx.
 */
import { registry } from './registry';
import type { CommandDecl } from './registry';

export interface MinibufferState {
  /** Whether the input line is active (vs. the idle status line). */
  open: boolean;
  query: string;
  /** Transient message in the echo area, cleared on the next interaction. */
  echo: { text: string; tone: 'info' | 'error' } | null;
}

let state: MinibufferState = { open: false, query: '', echo: null };
const listeners = new Set<() => void>();

function set(next: Partial<MinibufferState>): void {
  state = { ...state, ...next };
  for (const l of listeners) l();
}

/** How well a command matches a query; higher is better, 0 means no match. */
function score(command: CommandDecl, query: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const slash = command.slash?.toLowerCase();
  if (slash === q) return 100;
  if (slash?.startsWith(q)) return 50;
  const id = command.id.toLowerCase();
  const title = command.title.toLowerCase();
  // The verb half of `module.verb` is what a slash name is standing in for, so
  // an exact hit there ranks above an incidental substring in someone's title.
  const verb = id.split('.').pop() ?? '';
  if (verb === q) return 40;
  if (verb.startsWith(q)) return 20;
  if (id.includes(q)) return 10;
  if (title.includes(q)) return 5;
  return 0;
}

/**
 * Commands matching a minibuffer query, best first. A leading `/` is optional
 * and stripped — `/save` and `save` resolve the same way, so the slash is a
 * convention rather than a mode.
 *
 * Ties break by registration order (`registry.commands` order), which is what
 * makes "first-registered-wins" the documented rule for duplicate slash names.
 */
export function matchCommands(query: string, limit = 8): CommandDecl[] {
  const q = query.replace(/^\//, '');
  return registry.commands
    .map((command, index) => ({ command, index, score: score(command, q) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)
    .map((entry) => entry.command);
}

/** The command a query would run on Enter, or null when nothing matches. */
export function resolveCommand(query: string): CommandDecl | null {
  return matchCommands(query, 1)[0] ?? null;
}

export const minibuffer = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot(): MinibufferState {
    return state;
  },

  /** Activate the input line, optionally seeded (e.g. `/` from a keybinding). */
  open(query = ''): void {
    set({ open: true, query, echo: null });
  },
  close(): void {
    if (!state.open && state.query === '') return;
    set({ open: false, query: '' });
  },
  setQuery(query: string): void {
    set({ query });
  },
  /** Post a transient message to the echo area. */
  say(text: string, tone: 'info' | 'error' = 'info'): void {
    set({ echo: { text, tone } });
  },
  clearEcho(): void {
    if (state.echo === null) return;
    set({ echo: null });
  },

  /**
   * Run the current query's best match. Closes the input line and reports into
   * the echo area either way, so a mistyped command says so rather than
   * silently doing nothing.
   */
  async submit(): Promise<boolean> {
    const query = state.query;
    const command = resolveCommand(query);
    if (!command) {
      set({
        open: false,
        query: '',
        echo: { text: `No command matches "${query}"`, tone: 'error' },
      });
      return false;
    }
    set({ open: false, query: '', echo: null });
    try {
      await registry.runCommand(command.id);
      return true;
    } catch (err) {
      minibuffer.say(`${command.title} failed: ${String(err)}`, 'error');
      return false;
    }
  },
};
