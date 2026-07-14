/**
 * Pure derivations behind the **Build your agent** pane: reading an agent's "build"
 * (which abilities it uses) off its code + loadout, and turning that into game stats.
 *
 * This keeps the pane honestly **code-first** — the loadout slots and stats are a live
 * readout of what the agent code actually does (calls `retrieve()` ⇒ RAG lit, uses
 * `memory` ⇒ Memory lit), not a separate set of switches. See AgentBuilderPanel.tsx.
 */
import type { Loadout } from './games-api';

export type StatKey = 'tactics' | 'reasoning' | 'memory' | 'knowledge' | 'speed';

export const STAT_LABEL: Record<StatKey, string> = {
  tactics: 'Tactics',
  reasoning: 'Reasoning',
  memory: 'Memory',
  knowledge: 'Knowledge',
  speed: 'Speed',
};

// Which stat each game scores. The game picks the axis; the editor stays the same.
const GRADED: Record<string, StatKey> = {
  tictactoe: 'tactics',
  connect_four: 'tactics',
  holdem: 'reasoning',
  rag_race: 'knowledge',
  fighter: 'speed',
  vizdoom_toy: 'speed',
  vizdoom_duel: 'speed',
  arena: 'reasoning',
  bug_hunt: 'reasoning',
  code_golf: 'reasoning',
  test_duel: 'reasoning',
  tabular_fe: 'knowledge',
};

export const gradedStat = (gameId: string): StatKey => GRADED[gameId] ?? 'tactics';

/** The abilities the agent actually uses, read off its code + loadout. */
export interface Build {
  context: boolean;
  tools: number;
  rag: boolean;
  memory: boolean;
  model: string | null;
}

export function readBuild(lo: Loadout): Build {
  const code = lo.agent_code || '';
  return {
    context: (lo.context || '').trim().length > 0,
    tools: lo.tools?.length ?? 0,
    // Match actual *use* (a call / attribute access), not the words where they
    // appear in a comment or docstring (e.g. the default agent's "RAG, memory, …").
    rag: /\bretrieve\s*\(/.test(code),
    memory: /\bmemory\s*\./.test(code),
    model: lo.model ? String((lo.model.model as string) || 'custom') : null,
  };
}

export function stats(b: Build): Record<StatKey, number> {
  const clamp = (n: number) => Math.max(6, Math.min(99, Math.round(n)));
  return {
    tactics: clamp(34 + (b.tools > 0 ? 40 : 0)),
    reasoning: clamp(30 + (b.context ? 22 : 0) + (b.model ? 16 : 10)),
    memory: clamp(12 + (b.memory ? 62 : 0)),
    knowledge: clamp(14 + (b.rag ? 60 : 0)),
    speed: clamp(92 - (b.rag ? 20 : 0) - (b.memory ? 14 : 0) - (b.model ? 24 : 0)),
  };
}

/** A single 0–99 "power" number from a build, for the header readout. */
export function power(b: Build): number {
  const s = stats(b);
  const vals = Object.values(s);
  const avg = vals.reduce((a, v) => a + v, 0) / vals.length;
  return Math.round(avg + ((b.rag ? 1 : 0) + (b.memory ? 1 : 0)) * 4);
}
