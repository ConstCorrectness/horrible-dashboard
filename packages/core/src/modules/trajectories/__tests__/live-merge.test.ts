/**
 * Reconciling the live step buffer against a fetched snapshot.
 *
 * This is the whole correctness argument of the live view. Subscribing after the
 * fetch drops steps; merging without deduping shows them twice. Both failures are
 * silent — the pane renders a run that looks complete either way — so the merge is
 * pure and tested directly rather than inferred from the UI.
 */
import { describe, expect, it } from 'vitest';

import { mergeSteps, type LiveStep } from '../ws';

function s(seq: number, name = `t${seq}`): LiveStep {
  return {
    seq,
    kind: 'action',
    round: 0,
    role: null,
    name,
    args: null,
    result: null,
    ok: true,
    content: null,
    tokens: null,
    duration_ms: 1,
    gated: false,
    error: null,
    ts: 1000 + seq,
  } as LiveStep;
}

describe('mergeSteps', () => {
  it('drops buffered steps the snapshot already contains', () => {
    // The ordinary case: we subscribed, three steps arrived, then the fetch came
    // back already holding the first two.
    const merged = mergeSteps([s(0), s(1)], [s(0), s(1), s(2)]);
    expect(merged.map((x) => x.seq)).toEqual([0, 1, 2]);
  });

  it('keeps steps that arrived only on the wire', () => {
    // The race the subscribe-first ordering exists to close: step 2 landed between
    // the snapshot being taken and it reaching us.
    const merged = mergeSteps([s(0), s(1)], [s(2)]);
    expect(merged.map((x) => x.seq)).toEqual([0, 1, 2]);
  });

  it('orders by seq regardless of arrival order', () => {
    const merged = mergeSteps([s(3)], [s(1), s(0)]);
    expect(merged.map((x) => x.seq)).toEqual([0, 1, 3]);
  });

  it('lets a re-emitted step correct the one it replaces', () => {
    // Same seq twice is an amendment, not a duplicate — the store's own writes are
    // INSERT OR REPLACE, so the wire can legitimately carry a corrected step.
    const merged = mergeSteps([s(0, 'stale')], [s(0, 'fresh')]);
    expect(merged).toHaveLength(1);
    expect(merged[0].name).toBe('fresh');
  });

  it('returns the existing list untouched when nothing arrived', () => {
    const existing = [s(0)];
    expect(mergeSteps(existing, [])).toBe(existing);
  });

  it('handles a snapshot that is empty because the run just opened', () => {
    expect(mergeSteps([], [s(0), s(1)]).map((x) => x.seq)).toEqual([0, 1]);
  });

  it('does not renumber: seq is the server\'s, and the cursor depends on it', () => {
    // A gap is real information (a step we have not fetched), not something to
    // close by reindexing.
    expect(mergeSteps([s(0)], [s(5)]).map((x) => x.seq)).toEqual([0, 5]);
  });
});
