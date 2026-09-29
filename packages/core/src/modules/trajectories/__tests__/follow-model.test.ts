import { describe, expect, it } from 'vitest';

import type { ChatTurn } from '../../agent/chat-state';
import type { ContextBlock, RoundSnapshot } from '../../interpretability/store';
import type { TrajectoryStep } from '../api';
import {
  diffChips,
  diffRounds,
  familyOf,
  selectTurn,
  stepsByRound,
  tokensByFamily,
  turnRows,
} from '../panels/follow-model';

const block = (kind: string, hash: string, tokens = 10, extra: Partial<ContextBlock> = {}) =>
  ({
    kind,
    role:
      kind === 'user' || kind === 'history'
        ? 'user'
        : kind.includes('result') || kind.endsWith('_loaded')
          ? 'tool'
          : 'system',
    label: kind,
    content: hash,
    tokens,
    clipped: false,
    fullChars: hash.length,
    hash,
    ...extra,
  }) as ContextBlock;

const round = (n: number, blocks: ContextBlock[], extra: Partial<RoundSnapshot> = {}) =>
  ({
    round: n,
    blocks,
    tools: [],
    messageTokens: blocks.reduce((s, b) => s + b.tokens, 0),
    toolTokens: 0,
    totalTokens: blocks.reduce((s, b) => s + b.tokens, 0),
    toolsSelected: 0,
    toolBudget: 40,
    toolsTruncated: false,
    activeGroups: [],
    ...extra,
  }) as RoundSnapshot;

describe('families', () => {
  it('draws injected harness text in its catalog family', () => {
    expect(familyOf('skill_loaded')).toBe(familyOf('skills'));
    expect(familyOf('tools_loaded')).toBe(familyOf('guides'));
    expect(familyOf('mcp_result')).toBe(familyOf('tool_result'));
  });

  it('counts tool schemas toward the tools family', () => {
    const r = round(0, [block('guides', 'g', 30), block('system', 's', 100)], { toolTokens: 70 });
    const by = tokensByFamily(r);
    expect(by.get('tools')).toBe(100);
    expect(by.get('system')).toBe(100);
  });
});

describe('diffRounds', () => {
  const r0 = round(0, [block('system', 's'), block('user', 'u')], { activeGroups: ['fs'] });

  it('marks everything in round 0 as added', () => {
    expect(diffRounds(undefined, r0).added).toEqual([0, 1]);
  });

  it('finds appended blocks by hash, not by position', () => {
    const r1 = round(1, [
      block('system', 's'),
      block('user', 'u'),
      block('assistant', 'a'),
      block('skill_loaded', 'k', 400, { toolName: 'use_skill' }),
    ]);
    const d = diffRounds(r0, r1);
    expect(d.added).toEqual([2, 3]);
    expect(d.removed).toEqual([]);
  });

  it('reports a replaced note as one removed and one added', () => {
    const a = round(1, [block('system', 's'), block('dropped_tools', 'n1')]);
    const b = round(2, [block('system', 's'), block('dropped_tools', 'n2')]);
    const d = diffRounds(a, b);
    expect(d.added).toEqual([1]);
    expect(d.removed.map((x) => x.hash)).toEqual(['n1']);
  });

  it('is multiset-aware for identical blocks', () => {
    const a = round(1, [block('tool_result', 'same')]);
    const b = round(2, [block('tool_result', 'same'), block('tool_result', 'same')]);
    expect(diffRounds(a, b).added).toEqual([1]);
  });

  it('diffs active groups', () => {
    const r1 = round(1, [block('system', 's')], {
      activeGroups: ['fs', 'github'],
      tools: [{ name: 'github.search', group: 'github', tokens: 50 }],
    });
    const d = diffRounds(r0, r1);
    expect(d.groupsAdded).toEqual(['github']);
    expect(d.groupsRemoved).toEqual([]);
    const chips = diffChips(r1, d, false);
    expect(chips.some((c) => c.label === 'group github (1)' && c.family === 'tools')).toBe(true);
  });

  it('falls back to a content key for blocks without a hash', () => {
    const old = { ...block('system', 's'), hash: undefined };
    const d = diffRounds(round(0, [old]), round(1, [{ ...old }]));
    expect(d.added).toEqual([]);
  });

  it('turns a loaded skill into a + chip in the skills family', () => {
    const r1 = round(
      1,
      [
        block('system', 's'),
        block('user', 'u'),
        block('skill_loaded', 'k', 400, { toolName: 'use_skill' }),
      ],
      { activeGroups: ['fs'] },
    );
    const chips = diffChips(r1, diffRounds(r0, r1), false);
    expect(chips).toEqual([
      { sign: '+', family: 'skills', label: 'skill_loaded use_skill', tokens: 400 },
    ]);
  });
});

describe('stepsByRound', () => {
  const step = (seq: number, round: number | null, extra: Partial<TrajectoryStep> = {}) =>
    ({ seq, round, kind: 'action', name: 't', ...extra }) as TrajectoryStep;

  it('buckets by round, in seq order, dropping the goal step', () => {
    const by = stepsByRound([
      step(0, 0, { kind: 'message', role: 'user' }),
      step(3, 1),
      step(1, 0),
      step(2, 1),
    ]);
    expect(by.get(0)?.map((s) => s.seq)).toEqual([1]);
    expect(by.get(1)?.map((s) => s.seq)).toEqual([2, 3]);
  });
});

describe('turnRows', () => {
  const turns: ChatTurn[] = [
    { role: 'user', text: 'first question', turnId: 't1' },
    { role: 'assistant', text: 'a1', turnId: 't1' },
    { role: 'user', text: '/help', ephemeral: true },
    { role: 'user', text: 'second\nmore', turnId: 't2' },
    {
      role: 'assistant',
      text: '',
      turnId: 't2',
      subTurns: [{ turnId: 't2:coder:ab', agentId: 'coder' }],
    },
  ];

  it('lists turns newest first with sub-turns nested under their parent', () => {
    const rows = turnRows(turns, true);
    expect(rows.map((r) => [r.turnId, r.depth, r.title])).toEqual([
      ['t2', 0, 'second'],
      ['t2:coder:ab', 1, 'coder'],
      ['t1', 0, 'first question'],
    ]);
    expect(rows[0].running).toBe(true);
    expect(rows[1].running).toBe(true);
    expect(rows[2].running).toBe(false);
  });

  it('skips turns without an id', () => {
    expect(turnRows([{ role: 'assistant', text: 'old' }], false)).toEqual([]);
  });

  it('selects the pin, else the newest root', () => {
    const rows = turnRows(turns, false);
    expect(selectTurn(rows, null)).toBe('t2');
    expect(selectTurn(rows, 't1')).toBe('t1');
    expect(selectTurn([], null)).toBeNull();
  });
});
