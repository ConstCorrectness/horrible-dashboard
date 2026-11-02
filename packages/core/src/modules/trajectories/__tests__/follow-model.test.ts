import { describe, expect, it } from 'vitest';

import type { ChatTurn } from '../../agent/chat-state';
import { familyOf, selectTurn, turnRows } from '../panels/follow-model';

describe('families', () => {
  it('draws injected harness text in its catalog family', () => {
    expect(familyOf('skill_loaded')).toBe(familyOf('skills'));
    expect(familyOf('tools_loaded')).toBe(familyOf('guides'));
    expect(familyOf('mcp_result')).toBe(familyOf('tool_result'));
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
