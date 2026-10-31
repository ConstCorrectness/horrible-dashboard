/**
 * The activity timeline's reducers. What they guard is ordering — a thought, then
 * the prose the model wrote, then the call it made, in the order they happened —
 * and that nothing still looks like it is running once the turn is over.
 */
import { describe, expect, it } from 'vitest';

import {
  answerQuestion,
  applyApproval,
  applyPlan,
  applyQuestion,
  applyReasoning,
  applyToken,
  applyToolEnd,
  applyToolStart,
  finalizeTurn,
  legacySteps,
  timelineBlocks,
  turnPhase,
  type ActivityTurn,
} from '../turn-steps';

const empty = (): ActivityTurn => ({ text: '', steps: [] });

describe('turn-steps', () => {
  it('folds reasoning deltas into one open thought, closed by the first token', () => {
    let t = applyReasoning(empty(), 'Let me ', 10);
    t = applyReasoning(t, 'look.', 11);
    expect(t.steps).toEqual([{ kind: 'thought', text: 'Let me look.', startedAt: 10 }]);
    t = applyToken(t, 'Hi', 50);
    expect(t.steps?.[0]).toMatchObject({ endedAt: 50 });
    expect(t.text).toBe('Hi');
    // Reasoning after a closed thought starts a new one rather than reopening it.
    t = applyReasoning(t, 'again', 60);
    expect(t.steps).toHaveLength(2);
  });

  it('turns prose written before a call into a note, and resets the answer', () => {
    let t = applyToken(empty(), "I'll check the scene first. ", 1);
    t = applyToolStart(t, { callId: '0', name: 'files.read_file', args: { path: 'a' } }, 2);
    expect(t.text).toBe('');
    expect(t.steps?.map((s) => s.kind)).toEqual(['note', 'tool']);
    expect(t.steps?.[0]).toEqual({ kind: 'note', text: "I'll check the scene first." });
  });

  it('matches a tool_end to the newest open row with that id', () => {
    // Providers number calls per response, so round two reuses id "0".
    let t = applyToolStart(empty(), { callId: '0', name: 'a', args: {} }, 1);
    t = applyToolEnd(t, { callId: '0', ok: true, durationMs: 5 });
    t = applyToolStart(t, { callId: '0', name: 'b', args: {} }, 10);
    t = applyToolEnd(t, { callId: '0', ok: false, durationMs: 7, preview: '{"error":"x"}' });
    const tools = t.steps?.filter((s) => s.kind === 'tool');
    expect(tools?.map((s) => s.kind === 'tool' && s.status)).toEqual(['ok', 'error']);
    expect(tools?.[1]).toMatchObject({ name: 'b', durationMs: 7 });
  });

  it('marks a row waiting on approval, then denied', () => {
    let t = applyToolStart(empty(), { callId: 'c', name: 'files.write', args: {} }, 1);
    t = applyApproval(t, 'c');
    expect(turnPhase(t)).toEqual({ kind: 'approval', name: 'files.write' });
    t = applyToolEnd(t, { callId: 'c', ok: false, denied: true });
    expect(t.steps?.[0]).toMatchObject({ status: 'denied' });
  });

  it('finalize leaves nothing running', () => {
    let t = applyReasoning(empty(), 'hm', 1);
    t = applyToolStart(t, { callId: '0', name: 'x', args: {} }, 2);
    t = applyQuestion(t, { questionId: 'q', questions: [] }, 3);
    t = finalizeTurn(t, 99);
    expect(t.endedAt).toBe(99);
    expect(t.steps).toEqual([
      { kind: 'thought', text: 'hm', startedAt: 1, endedAt: 2 },
      expect.objectContaining({ kind: 'tool', status: 'interrupted' }),
      expect.objectContaining({ kind: 'question', dismissed: true }),
    ]);
  });

  it('records an answered question and leaves the waiting phase', () => {
    let t = applyQuestion(empty(), { questionId: 'q1', questions: [] }, 1);
    expect(turnPhase(t)).toEqual({ kind: 'question' });
    t = answerQuestion(t, 'q1', { 'A or B?': 'A' });
    expect(t.steps?.[0]).toMatchObject({ answers: { 'A or B?': 'A' } });
    expect(turnPhase(t)).toEqual({ kind: 'thinking' });
  });

  it('a plan is proposed and closes any open thought', () => {
    const t = applyPlan(applyReasoning(empty(), 'x', 1), '# Plan', 5);
    expect(t.plan).toEqual({ text: '# Plan', status: 'proposed' });
    expect(t.steps?.[0]).toMatchObject({ endedAt: 5 });
  });

  it('groups consecutive thoughts and calls, with notes and questions between', () => {
    let t = applyReasoning(empty(), 'a', 1);
    t = applyToolStart(t, { callId: '0', name: 'x', args: {} }, 2);
    t = applyToken(t, 'Now the other half.', 3);
    t = applyToolStart(t, { callId: '1', name: 'y', args: {} }, 4);
    t = applyQuestion(t, { questionId: 'q', questions: [] }, 5);
    const blocks = timelineBlocks(t.steps ?? []);
    expect(blocks.map((b) => b.kind)).toEqual(['activity', 'note', 'activity', 'question']);
    expect(blocks[0].kind === 'activity' && blocks[0].steps).toHaveLength(2);
  });

  it('drops a card tool row once it succeeds, and keeps it when it fails', () => {
    let t = applyToolStart(empty(), { callId: '0', name: 'update_todos', args: {} }, 1);
    // In flight: the row is held for ordering but not drawn.
    expect(timelineBlocks(t.steps ?? [])).toEqual([]);
    t = applyToolEnd(t, { callId: '0', ok: true, durationMs: 0 });
    expect(t.steps).toEqual([]);
    t = applyToolStart(t, { callId: '1', name: 'submit_plan', args: {} }, 2);
    t = applyToolEnd(t, { callId: '1', ok: false, preview: '{"error":"plan mode only"}' });
    expect(t.steps?.[0]).toMatchObject({ name: 'submit_plan', status: 'error' });
  });

  it('rebuilds a pre-timeline turn from its reasoning and action notes', () => {
    const steps = legacySteps({ reasoning: 'old thoughts', actions: ['Opened Clock'] });
    expect(steps).toEqual([
      { kind: 'thought', text: 'old thoughts', startedAt: 0, endedAt: 0 },
      expect.objectContaining({ kind: 'tool', label: 'Opened Clock', status: 'ok' }),
    ]);
  });
});
