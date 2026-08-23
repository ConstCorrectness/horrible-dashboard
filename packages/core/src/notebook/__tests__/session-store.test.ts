import { describe, expect, it } from 'vitest';

import { SessionStore } from '../SessionStore';
import type { Notebook } from '../types';

const notebook = (): Notebook => ({ path: 'main.ipynb', cells: [], metadata: {} });

/**
 * The kernel badge and the error message are two halves of one answer, and they used
 * to contradict each other: `kernel` starts at `starting` and only `opened` or a
 * `kernel_status` event ever moved it, so an open that failed rendered "● starting"
 * directly beside the error explaining why it hadn't.
 */
describe('SessionStore error handling', () => {
  it('marks the kernel dead when the open itself failed', () => {
    const store = new SessionStore('training', 'p:main.ipynb');
    expect(store.snapshot().kernel).toBe('starting');

    store.onError("project 'evals-starter' has no main.ipynb");

    expect(store.snapshot().kernel).toBe('dead');
    expect(store.snapshot().error).toContain('has no main.ipynb');
  });

  it('leaves a live kernel alone when the error is recoverable', () => {
    // The same `error` event carries a rejected `cells` op on a session that is up
    // and fine. Calling that kernel dead would be its own lie — and it would make the
    // pane look broken over a bad edit.
    const store = new SessionStore('training', 'p:main.ipynb');
    store.onOpened(notebook(), 'idle');
    expect(store.snapshot().sessionKey).not.toBeNull();

    store.onError('unknown cell id');

    expect(store.snapshot().kernel).toBe('idle');
    expect(store.snapshot().error).toBe('unknown cell id');
  });

  it('keeps the code so a pane can still self-heal on unknown_project', () => {
    const store = new SessionStore('training', 'p:main.ipynb');
    store.onError('unknown project: gone', 'unknown_project');
    expect(store.snapshot().errorCode).toBe('unknown_project');
    expect(store.snapshot().kernel).toBe('dead');
  });
});
