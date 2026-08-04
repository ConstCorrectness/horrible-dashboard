/**
 * Transcript compaction. The failure this guards is silent by construction: with
 * no cap, the provider truncates from the *front* of the message list, which is
 * where the system prompt and the group guides live — so a long conversation
 * doesn't error, it quietly stops following its own instructions.
 */
import { describe, expect, it } from 'vitest';

import { compactHistory, MAX_HISTORY_TURNS, MAX_TURN_CHARS } from '../history';
import type { AgentTurn } from '../orchestrator-client';

function conversation(n: number): AgentTurn[] {
  return Array.from({ length: n }, (_, i) => ({
    role: i % 2 === 0 ? ('user' as const) : ('assistant' as const),
    content: `message ${i}`,
  }));
}

describe('compactHistory', () => {
  it('passes a short conversation through untouched', () => {
    const turns = conversation(4);
    const { history, omitted } = compactHistory(turns);
    expect(omitted).toBe(0);
    expect(history).toEqual(turns);
  });

  it('keeps the most recent window and reports what it dropped', () => {
    const { history, omitted } = compactHistory(conversation(30));
    expect(omitted).toBe(30 - MAX_HISTORY_TURNS);
    // marker + window
    expect(history).toHaveLength(MAX_HISTORY_TURNS + 1);
    expect(history[history.length - 1].content).toBe('message 29');
    expect(history[1].content).toBe(`message ${30 - MAX_HISTORY_TURNS}`);
  });

  it('replaces the dropped head with one marker the model can act on', () => {
    const { history } = compactHistory(conversation(30));
    // `user`, because `_history_messages` keeps only user/assistant text and an
    // assistant announcing its own amnesia reads as something it said.
    expect(history[0].role).toBe('user');
    expect(history[0].content).toContain('18 earlier messages');
    expect(history[0].content).toMatch(/Ask if you need something/);
  });

  it('caps one oversized turn without dropping the whole conversation', () => {
    const huge = 'x'.repeat(MAX_TURN_CHARS * 3);
    const { history, omitted } = compactHistory([
      { role: 'user', content: huge },
      { role: 'assistant', content: 'ok' },
    ]);
    expect(omitted).toBe(0);
    expect(history[0].content.length).toBeLessThanOrEqual(MAX_TURN_CHARS);
    // Middle-elided: a pasted blob's head says what it is and its tail holds the
    // error; the middle is the repetitive part.
    expect(history[0].content).toContain('characters omitted');
    expect(history[1].content).toBe('ok');
  });

  it('does not mutate the turns it was given', () => {
    const turns = conversation(2);
    compactHistory(turns);
    expect(turns).toEqual(conversation(2));
  });

  it('honours an explicit limit', () => {
    const { history, omitted } = compactHistory(conversation(10), 4);
    expect(omitted).toBe(6);
    expect(history).toHaveLength(5);
  });
});
