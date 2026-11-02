/**
 * The Follow section's data model: the source families every view of a context
 * colours by, and the turn rail built from the chat transcript (`ChatTurn`), which
 * knows the turn ids and sub-turns. The request inspector's own model is
 * request-model.ts.
 *
 * Kept free of React and fetches so it runs in core's DOM-less vitest.
 */
import type { ChatTurn } from '../../agent/chat-state';

// --- sources -----------------------------------------------------------------

/**
 * Colour families. A loaded skill is drawn in the skill catalog's hue and a loaded
 * tool group in the tool guides' hue, so injected text reads as the same thing as
 * the catalog entry that offered it.
 */
export type SourceFamily =
  | 'system'
  | 'skills'
  | 'tools'
  | 'workspace'
  | 'editor'
  | 'history'
  | 'user'
  | 'assistant'
  | 'result'
  | 'loop';

const FAMILY: Record<string, SourceFamily> = {
  system: 'system',
  skills: 'skills',
  skill_loaded: 'skills',
  guides: 'tools',
  tools_loaded: 'tools',
  workspace: 'workspace',
  editor: 'editor',
  history: 'history',
  user: 'user',
  assistant: 'assistant',
  tool_result: 'result',
  mcp_result: 'result',
  nudge: 'loop',
  dropped_tools: 'loop',
};

export function familyOf(kind: string): SourceFamily {
  return FAMILY[kind] ?? 'history';
}

/** Legend order: the harness's own text first, then the conversation, then the loop. */
export const FAMILY_ORDER: SourceFamily[] = [
  'system',
  'skills',
  'tools',
  'workspace',
  'editor',
  'history',
  'user',
  'assistant',
  'result',
  'loop',
];

export const FAMILY_LABEL: Record<SourceFamily, string> = {
  system: 'System',
  skills: 'Skills',
  tools: 'Tools',
  workspace: 'Workspace',
  editor: 'Editor',
  history: 'History',
  user: 'User',
  assistant: 'Assistant',
  result: 'Results',
  loop: 'Loop notes',
};

// --- the turn rail -------------------------------------------------------------

export interface TurnRow {
  turnId: string;
  /** First line of the user's message, or the delegate's agent id. */
  title: string;
  agentId: string | null;
  depth: 0 | 1;
  /** Undefined while running; a sub-turn's `ok` once it reports. */
  ok?: boolean;
  running: boolean;
}

/**
 * The followed conversation's turns, newest first, each with its delegated
 * sub-turns right under it. Only turns with an id appear: slash output and
 * messages saved before turn ids existed have nothing to join on.
 */
export function turnRows(turns: ChatTurn[], busy: boolean): TurnRow[] {
  const rows: TurnRow[] = [];
  let lastAssistant = -1;
  turns.forEach((t, i) => {
    if (t.role === 'assistant' && t.turnId) lastAssistant = i;
  });
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const t = turns[i];
    if (t.role !== 'assistant' || !t.turnId) continue;
    const user = turns
      .slice(0, i)
      .reverse()
      .find((u) => u.role === 'user' && u.turnId === t.turnId);
    const running = busy && i === lastAssistant;
    rows.push({
      turnId: t.turnId,
      title: firstLine(user?.text ?? t.text) || '(empty)',
      agentId: null,
      depth: 0,
      running,
    });
    for (const sub of t.subTurns ?? []) {
      rows.push({
        turnId: sub.turnId,
        title: sub.agentId,
        agentId: sub.agentId,
        depth: 1,
        ok: sub.ok,
        running: sub.ok === undefined && running,
      });
    }
  }
  return rows;
}

function firstLine(text: string): string {
  const line = text.split('\n').find((l) => l.trim()) ?? '';
  return line.length > 90 ? `${line.slice(0, 89)}…` : line;
}

/** Which turn to show: the pin when there is one, otherwise the newest. */
export function selectTurn(rows: TurnRow[], pinned: string | null): string | null {
  if (pinned) return pinned;
  return rows.find((r) => r.depth === 0)?.turnId ?? null;
}
