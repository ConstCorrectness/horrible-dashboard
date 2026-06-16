/**
 * Tracks the mounted terminals so the `terminal.*` commands (clear/kill/focus)
 * can act on the active one and cycle focus. Ordered by mount; the active one is
 * the most recently focused.
 */
export interface TerminalHandle {
  id: string;
  clear: () => void;
  focus: () => void;
  /** Send input to the PTY (e.g. a command + newline). */
  write: (data: string) => void;
  /** Recent scrollback as text (for the agent's `terminal.read`). */
  read: () => string;
}

const terminals: TerminalHandle[] = [];
let activeId: string | null = null;

export function registerTerminal(handle: TerminalHandle): void {
  terminals.push(handle);
  activeId = handle.id;
}

export function unregisterTerminal(id: string): void {
  const idx = terminals.findIndex((t) => t.id === id);
  if (idx >= 0) terminals.splice(idx, 1);
  if (activeId === id) activeId = terminals[terminals.length - 1]?.id ?? null;
}

export function setActiveTerminal(id: string): void {
  if (terminals.some((t) => t.id === id)) activeId = id;
}

export function getActiveTerminal(): TerminalHandle | null {
  return terminals.find((t) => t.id === activeId) ?? null;
}

export function getTerminal(id: string): TerminalHandle | null {
  return terminals.find((t) => t.id === id) ?? null;
}

export function listTerminals(): { id: string; active: boolean }[] {
  return terminals.map((t) => ({ id: t.id, active: t.id === activeId }));
}

/** The terminal `step` positions after the active one (wraps). */
export function siblingTerminal(step: number): TerminalHandle | null {
  if (terminals.length === 0) return null;
  const idx = Math.max(
    0,
    terminals.findIndex((t) => t.id === activeId),
  );
  const next = (idx + step + terminals.length) % terminals.length;
  return terminals[next] ?? null;
}
