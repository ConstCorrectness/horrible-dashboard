/**
 * Transcript compaction — what the chat replays to the model each turn.
 *
 * The widget used to send the **entire** transcript every turn. Nothing caught
 * the overflow either: `agent.orchestrator.contextSize` defaults to the provider
 * default, so a long conversation didn't error, it silently lost its head — the
 * provider truncates from the front, which is exactly where the system prompt and
 * the group guides live. A five-turn conversation on a small local model would
 * quietly stop obeying its own instructions, with no signal anywhere.
 *
 * So: keep a bounded window verbatim, replace what falls out with one visible
 * marker, and cap any single oversized turn (one pasted stack trace can be larger
 * than the rest of the conversation combined).
 *
 * The marker is a **user** message on purpose. `_history_messages` keeps only
 * user/assistant text, and an assistant message announcing its own amnesia reads
 * as something the assistant said. Bracketed and in the user's voice, it is
 * unambiguously an annotation — and it tells the model what to do about it
 * (ask), instead of leaving a hole it will confidently fill in.
 *
 * Pure and separate from the widget so it is testable without a DOM.
 */
import type { AgentTurn } from './orchestrator-client';

/** Exchanges kept verbatim (12 messages ≈ 6 back-and-forths). */
export const MAX_HISTORY_TURNS = 12;

/** Per-message ceiling before the middle is elided. */
export const MAX_TURN_CHARS = 4000;

export interface CompactedHistory {
  history: AgentTurn[];
  /** Messages dropped from the head — what the marker reports, and what the UI shows. */
  omitted: number;
}

/**
 * Elide the middle rather than the tail: the head of a pasted blob says what it
 * is and the tail usually holds the error, while the middle is the repetitive
 * part. Cutting the tail loses the punchline.
 */
function clamp(text: string): string {
  if (text.length <= MAX_TURN_CHARS) return text;
  const half = Math.floor((MAX_TURN_CHARS - 40) / 2);
  const cut = text.length - MAX_TURN_CHARS + 40;
  return `${text.slice(0, half)}\n…[${cut} characters omitted]…\n${text.slice(-half)}`;
}

function marker(omitted: number): AgentTurn {
  return {
    role: 'user',
    content:
      `[${omitted} earlier message${omitted === 1 ? '' : 's'} in this conversation ` +
      `were omitted to fit the context window. Ask if you need something from before ` +
      `this point.]`,
  };
}

export function compactHistory(
  turns: AgentTurn[],
  limit: number = MAX_HISTORY_TURNS,
): CompactedHistory {
  const clamped = turns.map((t) => ({ ...t, content: clamp(t.content) }));
  if (clamped.length <= limit) return { history: clamped, omitted: 0 };
  const kept = clamped.slice(-limit);
  const omitted = clamped.length - kept.length;
  return { history: [marker(omitted), ...kept], omitted };
}
