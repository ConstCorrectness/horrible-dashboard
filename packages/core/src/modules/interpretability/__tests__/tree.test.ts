import { describe, expect, it } from 'vitest';

import { agentsIn, buildTurnTree, type TurnSnapshot } from '../store';

/** A captured turn with only the fields the tree logic reads. */
function turn(over: Partial<TurnSnapshot> & { turnId: string }): TurnSnapshot {
  return {
    agentId: 'main',
    agentName: 'Orchestrator',
    model: 'm',
    provider: 'p',
    startedAt: 0,
    rounds: [],
    parentTurnId: null,
    toolGroups: null,
    permissionMode: null,
    kind: 'local',
    peerId: null,
    sentPrompt: null,
    exact: true,
    tokenizerRepo: null,
    tokenizerSource: 'model',
    requestedNumCtx: null,
    modelContextLength: null,
    temperature: null,
    topP: null,
    maxTokens: null,
    ...over,
  };
}

describe('buildTurnTree', () => {
  it('nests a delegated turn under the turn that spawned it', () => {
    const rows = buildTurnTree([
      turn({ turnId: 'A' }),
      turn({ turnId: 'A:coder:1', parentTurnId: 'A', agentId: 'coder', startedAt: 1 }),
    ]);
    expect(rows.map((r) => [r.turn.turnId, r.depth])).toEqual([
      ['A', 0],
      ['A:coder:1', 1],
    ]);
  });

  it('orders sibling delegations by when they started', () => {
    const rows = buildTurnTree([
      turn({ turnId: 'A' }),
      turn({ turnId: 'A:second', parentTurnId: 'A', startedAt: 20 }),
      turn({ turnId: 'A:first', parentTurnId: 'A', startedAt: 10 }),
    ]);
    expect(rows.map((r) => r.turn.turnId)).toEqual(['A', 'A:first', 'A:second']);
  });

  it('promotes an orphan to a root rather than dropping it', () => {
    // The parent aged out of the 25-turn ring. Hiding the child would silently
    // lose a captured turn, which is worse than showing it without its context.
    const rows = buildTurnTree([
      turn({ turnId: 'A:coder:1', parentTurnId: 'gone', agentId: 'coder' }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].depth).toBe(0);
  });

  it('survives a cycle instead of blowing the stack', () => {
    const rows = buildTurnTree([
      turn({ turnId: 'A', parentTurnId: 'B' }),
      turn({ turnId: 'B', parentTurnId: 'A' }),
    ]);
    // Both are children of a present parent, so neither is a root and nothing is
    // emitted — but crucially it terminates.
    expect(rows.length).toBeLessThanOrEqual(2);
  });

  it('handles deeper chains without assuming one-level delegation', () => {
    // Built-ins are one level deep, but plugin agents set their own can_delegate.
    const rows = buildTurnTree([
      turn({ turnId: 'A' }),
      turn({ turnId: 'B', parentTurnId: 'A' }),
      turn({ turnId: 'C', parentTurnId: 'B' }),
    ]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2]);
  });

  it('places a peer handoff as a leaf under its caller', () => {
    const rows = buildTurnTree([
      turn({ turnId: 'A' }),
      turn({
        turnId: 'A:peer:n7',
        parentTurnId: 'A',
        kind: 'peer',
        peerId: 'n7',
        agentId: 'peer:n7',
      }),
    ]);
    expect(rows[1].depth).toBe(1);
    expect(rows[1].turn.kind).toBe('peer');
  });
});

describe('agentsIn', () => {
  it('lists each distinct agent once, preferring its display name', () => {
    expect(
      agentsIn([
        turn({ turnId: 'A', agentId: 'main', agentName: 'Orchestrator' }),
        turn({ turnId: 'B', agentId: 'coder', agentName: 'Coder' }),
        turn({ turnId: 'C', agentId: 'coder', agentName: 'Coder' }),
      ]),
    ).toEqual([
      { id: 'main', name: 'Orchestrator' },
      { id: 'coder', name: 'Coder' },
    ]);
  });

  it('falls back to the id when no name was captured', () => {
    expect(agentsIn([turn({ turnId: 'A', agentId: 'x', agentName: '' })])).toEqual([
      { id: 'x', name: 'x' },
    ]);
  });
});
