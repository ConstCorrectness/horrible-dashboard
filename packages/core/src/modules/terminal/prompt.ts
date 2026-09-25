/**
 * When has a command typed into a terminal finished? A PTY has no exit status to
 * offer mid-session, so `terminal.exec` infers it from the screen. Kept apart from
 * agentTools.ts so it can be unit-tested without importing the xterm pane.
 */

/** Output counts as finished once it has been quiet this long. */
export const QUIET_MS = 700;

/** A shell prompt (or a REPL's) waiting on the last line: `PS C:\x>`, `user@h:~$`,
 *  `#`, zsh's `%`, Python's `>>>`. */
const PROMPT = /[>$#%]\s*$/;

/** Whether the shell is back at a prompt: output has gone quiet *and* the last line
 *  is a prompt. Quiet alone is not enough — a script that prints, sleeps, then
 *  prints again is quiet in the middle. */
export function atPrompt(scrollback: string, quietFor: number): boolean {
  if (quietFor < QUIET_MS) return false;
  const lastLine = scrollback.trimEnd().split('\n').pop() ?? '';
  return PROMPT.test(lastLine);
}
