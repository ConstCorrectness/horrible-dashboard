/**
 * The unsaved-content cache is the only thing that knows a *background* tab is
 * dirty — `buffers.ts` unregisters on unmount, so it answers for the one mounted
 * buffer and nothing else. These pin the two behaviours a host depends on: the
 * dirty answer survives the component, and a discard actually discards.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  forgetUnsaved,
  isSourceDirty,
  listDirtySources,
  readUnsaved,
  subscribeUnsaved,
  writeUnsaved,
} from '../unsaved';

const A = 'workspace-file:/repo/a.ts';
const B = 'workspace-file:/repo/b.ts';

beforeEach(() => {
  forgetUnsaved(A);
  forgetUnsaved(B);
});

describe('unsaved cache', () => {
  it('reports a source dirty without a mounted buffer', () => {
    expect(isSourceDirty(A)).toBe(false);
    writeUnsaved(A, { content: 'edited', dirty: true, proposing: false });
    expect(isSourceDirty(A)).toBe(true);
    expect(readUnsaved(A)?.content).toBe('edited');
  });

  it('does not call a clean-but-cached source dirty', () => {
    // A proposal in flight caches content with `dirty: false`. That is not an
    // unsaved edit and must not raise a dirty dot.
    writeUnsaved(A, { content: 'proposed', dirty: false, proposing: true });
    expect(isSourceDirty(A)).toBe(false);
    expect(listDirtySources()).not.toContain(A);
  });

  it('forgets on discard, so declined changes do not resurrect', () => {
    writeUnsaved(A, { content: 'discarded', dirty: true, proposing: false });
    forgetUnsaved(A);
    expect(readUnsaved(A)).toBeUndefined();
    expect(isSourceDirty(A)).toBe(false);
  });

  it('lists every dirty source for a save-all walk', () => {
    writeUnsaved(A, { content: 'a', dirty: true, proposing: false });
    writeUnsaved(B, { content: 'b', dirty: false, proposing: false });
    expect(listDirtySources()).toEqual([A]);
  });

  it('notifies subscribers on write and on forget', () => {
    const seen = vi.fn();
    const off = subscribeUnsaved(seen);
    writeUnsaved(A, { content: 'x', dirty: true, proposing: false });
    expect(seen).toHaveBeenCalledTimes(1);
    forgetUnsaved(A);
    expect(seen).toHaveBeenCalledTimes(2);
    // Forgetting what was never there changes nothing, so it notifies nobody.
    forgetUnsaved(A);
    expect(seen).toHaveBeenCalledTimes(2);
    off();
    writeUnsaved(A, { content: 'y', dirty: true, proposing: false });
    expect(seen).toHaveBeenCalledTimes(2);
  });
});
