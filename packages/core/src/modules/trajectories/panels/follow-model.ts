/**
 * The Follow section's data model: pure functions over the three records one chat
 * turn leaves behind, joined on its `turnId`.
 *
 * - the chat transcript (`ChatTurn`), which knows the turn ids and sub-turns;
 * - interpretability's `TurnSnapshot`, which knows what the model was *shown* each
 *   round, block by block, with where each block came from;
 * - the trajectory run, which knows what the agent *did* each round.
 *
 * Kept free of React and fetches so it runs in core's DOM-less vitest.
 */
import type { ChatTurn } from '../../agent/chat-state';
import type { ContextBlock, RoundSnapshot, ToolEntry } from '../../interpretability/store';
import type { TrajectoryStep } from '../api';

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

/** Tokens per family for one round. Tool *schemas* count toward `tools`: they are
 *  the other half of what the tool family puts in front of the model. */
export function tokensByFamily(round: RoundSnapshot): Map<SourceFamily, number> {
  const out = new Map<SourceFamily, number>();
  for (const block of round.blocks) {
    const fam = familyOf(block.kind);
    out.set(fam, (out.get(fam) ?? 0) + block.tokens);
  }
  if (round.toolTokens) out.set('tools', (out.get('tools') ?? 0) + round.toolTokens);
  return out;
}

// --- round diff --------------------------------------------------------------

/** A block's identity across rounds. `hash` when the backend sent one; older turns
 *  fall back to role + kind + a content prefix, which is right for everything but
 *  two identical long blocks — acceptable for turns captured before hashes. */
export function blockKey(block: ContextBlock): string {
  return (
    block.hash ||
    `${block.role}\u0000${block.kind}\u0000${block.fullChars}\u0000${block.content.slice(0, 96)}`
  );
}

export interface RoundDiff {
  /** Indexes into the current round's `blocks` that were not in the previous one. */
  added: number[];
  /** Blocks of the previous round that are gone (a replaced note, a trim). */
  removed: ContextBlock[];
  groupsAdded: string[];
  groupsRemoved: string[];
  toolsAdded: string[];
  toolsRemoved: string[];
}

/** What changed between two consecutive rounds. Multiset-aware: two identical
 *  blocks in a row are two blocks, and only the extra one is "added". Round 0 has
 *  no predecessor, so everything in it is added. */
export function diffRounds(prev: RoundSnapshot | undefined, cur: RoundSnapshot): RoundDiff {
  const before = new Map<string, number>();
  for (const b of prev?.blocks ?? []) before.set(blockKey(b), (before.get(blockKey(b)) ?? 0) + 1);

  const added: number[] = [];
  cur.blocks.forEach((b, i) => {
    const key = blockKey(b);
    const left = before.get(key) ?? 0;
    if (left > 0) before.set(key, left - 1);
    else added.push(i);
  });

  const removed: ContextBlock[] = [];
  if (prev) {
    const remaining = new Map(before);
    for (const b of prev.blocks) {
      const key = blockKey(b);
      const left = remaining.get(key) ?? 0;
      if (left > 0) {
        removed.push(b);
        remaining.set(key, left - 1);
      }
    }
  }

  const setDiff = (a: string[], b: string[]) => a.filter((x) => !b.includes(x));
  const prevGroups = prev?.activeGroups ?? [];
  const prevTools = (prev?.tools ?? []).map((t) => t.name);
  const curTools = cur.tools.map((t) => t.name);
  return {
    added,
    removed,
    groupsAdded: prev ? setDiff(cur.activeGroups, prevGroups) : [...cur.activeGroups],
    groupsRemoved: setDiff(prevGroups, cur.activeGroups),
    toolsAdded: prev ? setDiff(curTools, prevTools) : [],
    toolsRemoved: setDiff(prevTools, curTools),
  };
}

/** One chip on a round band. `sign` is what happened to the context. */
export interface DiffChip {
  sign: '+' | '−' | '•';
  family: SourceFamily;
  label: string;
  tokens?: number;
}

/** A round's diff as the chips the band shows, loop-significant things first. */
export function diffChips(cur: RoundSnapshot, diff: RoundDiff, round0: boolean): DiffChip[] {
  const chips: DiffChip[] = [];
  if (!round0) {
    for (const i of diff.added) {
      const b = cur.blocks[i];
      if (b.kind === 'assistant') continue; // the call itself is the step list's job
      chips.push({
        sign: b.kind === 'nudge' ? '•' : '+',
        family: familyOf(b.kind),
        label: b.toolName ? `${b.kind} ${b.toolName}` : b.kind,
        tokens: b.tokens,
      });
    }
    for (const b of diff.removed) {
      chips.push({ sign: '−', family: familyOf(b.kind), label: b.kind, tokens: b.tokens });
    }
  }
  for (const g of diff.groupsAdded) {
    const n = cur.tools.filter((t) => t.group === g).length;
    chips.push({ sign: '+', family: 'tools', label: `group ${g}${n ? ` (${n})` : ''}` });
  }
  for (const g of diff.groupsRemoved)
    chips.push({ sign: '−', family: 'tools', label: `group ${g}` });
  return chips;
}

// --- steps -------------------------------------------------------------------

/** Trajectory steps bucketed by the round that produced them. Steps with no round
 *  (imported runs) land in round 0. The goal step (seq 0, the user's message) is
 *  dropped: the prompt column already shows it. */
export function stepsByRound(steps: TrajectoryStep[]): Map<number, TrajectoryStep[]> {
  const out = new Map<number, TrajectoryStep[]>();
  for (const step of steps) {
    if (step.seq === 0 && step.kind === 'message' && step.role === 'user') continue;
    const r = step.round ?? 0;
    const list = out.get(r) ?? [];
    list.push(step);
    out.set(r, list);
  }
  for (const list of out.values()) list.sort((a, b) => a.seq - b.seq);
  return out;
}

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

/** Tools offered this round, grouped for the list, biggest group first. */
export function toolGroups(
  tools: ToolEntry[],
): { group: string; tools: ToolEntry[]; tokens: number }[] {
  const by = new Map<string, ToolEntry[]>();
  for (const t of tools) by.set(t.group || 'other', [...(by.get(t.group || 'other') ?? []), t]);
  return [...by.entries()]
    .map(([group, list]) => ({
      group,
      tools: list,
      tokens: list.reduce((n, t) => n + t.tokens, 0),
    }))
    .sort((a, b) => b.tokens - a.tokens);
}
