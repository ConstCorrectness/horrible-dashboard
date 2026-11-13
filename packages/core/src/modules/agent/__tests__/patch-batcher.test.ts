import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createPatchBatcher,
  FLUSH_FALLBACK_MS,
  frameSchedule,
  type Schedule,
} from '../patch-batcher';

/** A schedule the test fires by hand. */
function manual(): { schedule: Schedule; fire: () => void; scheduled: () => number } {
  let run: (() => void) | null = null;
  let count = 0;
  return {
    schedule: (r) => {
      run = r;
      count++;
      return () => {
        run = null;
      };
    },
    fire: () => run?.(),
    scheduled: () => count,
  };
}

describe('createPatchBatcher', () => {
  it('applies many deferred patches as one write', () => {
    const m = manual();
    let value = '';
    let writes = 0;
    const b = createPatchBatcher<string>((fn) => {
      value = fn(value);
      writes++;
    }, m.schedule);

    for (const ch of 'hello') b.defer((v) => v + ch);
    expect(writes).toBe(0);
    expect(m.scheduled()).toBe(1);

    m.fire();
    expect(value).toBe('hello');
    expect(writes).toBe(1);
  });

  it('keeps arrival order when an immediate patch follows deferred ones', () => {
    const m = manual();
    let value: string[] = [];
    let writes = 0;
    const b = createPatchBatcher<string[]>((fn) => {
      value = fn(value);
      writes++;
    }, m.schedule);

    b.defer((v) => [...v, 'token-1']);
    b.defer((v) => [...v, 'token-2']);
    b.now((v) => [...v, 'tool-start']);

    expect(value).toEqual(['token-1', 'token-2', 'tool-start']);
    expect(writes).toBe(1);

    // The frame that was pending has nothing left to do.
    m.fire();
    expect(writes).toBe(1);
  });

  it('schedules again after a flush', () => {
    const m = manual();
    let value = 0;
    const b = createPatchBatcher<number>((fn) => {
      value = fn(value);
    }, m.schedule);

    b.defer((v) => v + 1);
    m.fire();
    b.defer((v) => v + 1);
    expect(m.scheduled()).toBe(2);
    b.flush();
    expect(value).toBe(2);
  });

  it('does not write when there is nothing queued', () => {
    let writes = 0;
    const b = createPatchBatcher<number>(() => {
      writes++;
    }, manual().schedule);
    b.flush();
    expect(writes).toBe(0);
  });
});

describe('frameSchedule', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('still runs when no animation frame ever arrives', () => {
    // A hidden pane or a backgrounded tab: rAF is accepted and never called.
    vi.stubGlobal('requestAnimationFrame', () => 1);
    vi.stubGlobal('cancelAnimationFrame', () => {});
    const run = vi.fn();
    frameSchedule(run);
    vi.advanceTimersByTime(FLUSH_FALLBACK_MS - 1);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('runs once when both the frame and the timer fire', () => {
    let frame: (() => void) | null = null;
    vi.stubGlobal('requestAnimationFrame', (cb: () => void) => {
      frame = cb;
      return 1;
    });
    vi.stubGlobal('cancelAnimationFrame', () => {});
    const run = vi.fn();
    frameSchedule(run);
    frame!();
    vi.advanceTimersByTime(FLUSH_FALLBACK_MS * 2);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('does not run once cancelled', () => {
    vi.stubGlobal('requestAnimationFrame', () => 1);
    vi.stubGlobal('cancelAnimationFrame', () => {});
    const run = vi.fn();
    frameSchedule(run)();
    vi.advanceTimersByTime(FLUSH_FALLBACK_MS * 2);
    expect(run).not.toHaveBeenCalled();
  });
});
