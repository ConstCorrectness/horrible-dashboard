import { describe, expect, it } from 'vitest';

import { gradedStat, power, readBuild, stats } from '../agentBuild';
import type { Loadout } from '../games-api';

function lo(partial: Partial<Loadout>): Loadout {
  return {
    kind: 'llm',
    game_id: 't',
    context: '',
    tools: [],
    model: null,
    agent_code: '',
    ...partial,
  };
}

describe('agent build derivation (code-first readout)', () => {
  it('reads abilities off the agent code + loadout', () => {
    const b = readBuild(
      lo({
        context: 'you are playing',
        tools: [
          { name: 'scan', description: '', code: '', parameters: {}, required: [] },
          { name: 'fork', description: '', code: '', parameters: {}, required: [] },
        ],
        agent_code:
          'async def my_agent(o,c):\n  d = await c.retrieve(o)\n  return c.memory.recall()',
        model: { model: 'gemma4:e2b' },
      }),
    );
    expect(b.context).toBe(true);
    expect(b.tools).toBe(2);
    expect(b.rag).toBe(true); // calls retrieve(
    expect(b.memory).toBe(true); // uses memory
    expect(b.model).toBe('gemma4:e2b');
  });

  it('a bare agent lights nothing up', () => {
    const b = readBuild(
      lo({ agent_code: 'def my_agent(o, c):\n  return o["legal_actions"][0]["id"]' }),
    );
    expect(b).toEqual({ context: false, tools: 0, rag: false, memory: false, model: null });
  });

  it('"rag"/"memory" in a docstring do not light abilities (usage, not prose)', () => {
    // The default agent's docstring mentions "RAG, memory, search" — must not count.
    const doc = readBuild(
      lo({
        agent_code:
          'async def my_agent(o, c):\n  """build anything (RAG, memory, search)."""\n  return await c.decide(o)',
      }),
    );
    expect(doc.rag).toBe(false);
    expect(doc.memory).toBe(false);
    expect(readBuild(lo({ agent_code: 'x = c.memory.recall()' })).memory).toBe(true);
  });

  it('equipping RAG trades knowledge for speed', () => {
    const base = stats(readBuild(lo({})));
    const withRag = stats(readBuild(lo({ agent_code: 'x = retrieve("q")' })));
    expect(withRag.knowledge).toBeGreaterThan(base.knowledge);
    expect(withRag.speed).toBeLessThan(base.speed);
  });

  it('tools drive tactics; power rises with more abilities', () => {
    const bare = readBuild(lo({}));
    const loaded = readBuild(
      lo({
        tools: [{ name: 'a', description: '', code: '', parameters: {}, required: [] }],
        agent_code: 'retrieve(); memory.recall()',
        model: { model: 'x' },
      }),
    );
    expect(stats(loaded).tactics).toBeGreaterThan(stats(bare).tactics);
    expect(power(loaded)).toBeGreaterThan(power(bare));
  });

  it('each game grades an axis; unknown games default to tactics', () => {
    expect(gradedStat('rag_race')).toBe('knowledge');
    expect(gradedStat('holdem')).toBe('reasoning');
    expect(gradedStat('vizdoom_duel')).toBe('speed');
    expect(gradedStat('tictactoe')).toBe('tactics');
    expect(gradedStat('no-such-game')).toBe('tactics');
  });
});
