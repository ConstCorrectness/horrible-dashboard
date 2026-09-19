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

describe('SessionStore execution provenance', () => {
  it('marks edited cells and their transitive consumers stale until they run', () => {
    const store = new SessionStore('notebook', 'nb:main.ipynb');
    // `onCellsChanged` leaves the session unopened, so this unit test exercises
    // the local provenance update without trying to open a browser websocket.
    store.onCellsChanged({
      path: 'main.ipynb',
      metadata: {},
      cells: [
        { id: 'a', cell_type: 'code', source: 'x = 1', outputs: [] },
        { id: 'b', cell_type: 'code', source: 'y = x + 1', outputs: [] },
        { id: 'c', cell_type: 'code', source: 'z = y + 1', outputs: [] },
      ],
    });
    store.onGraph(
      [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'c' },
      ],
      [],
    );

    store.applyLocal(
      [{ op: 'edit', cellId: 'a', source: 'x = 2' }],
      store.snapshot().cells.map((cell) => (cell.id === 'a' ? { ...cell, source: 'x = 2' } : cell)),
    );
    expect(store.snapshot().staleCells).toEqual(expect.arrayContaining(['a', 'b', 'c']));

    store.onExecutionState('a', 'running');
    store.onExecutionState('a', 'done', 1);
    expect(store.snapshot().staleCells).not.toContain('a');
    expect(store.snapshot().staleCells).toEqual(expect.arrayContaining(['b', 'c']));
    expect(store.snapshot().executionHistory[0]).toMatchObject({ cellId: 'a', state: 'done' });
  });
});

describe('SessionStore display updates', () => {
  it('replaces one output in place and ignores an index that no longer exists', () => {
    const store = new SessionStore('training', 'p:main.ipynb');
    store.onCellsChanged({
      path: 'main.ipynb',
      metadata: {},
      cells: [{ id: 'a', cell_type: 'code', source: 'trainer.train()', outputs: [] }],
    });
    store.onOutput('a', { output_type: 'display_data', data: { 'text/plain': '2/50' } });
    store.onOutput('a', { output_type: 'stream', name: 'stdout', text: 'hi' });

    store.onOutputUpdated('a', 0, {
      output_type: 'display_data',
      data: { 'text/plain': '50/50' },
    });
    store.onOutputUpdated('a', 7, { output_type: 'display_data', data: {} });

    const outputs = store.snapshot().cells[0]?.outputs ?? [];
    expect(outputs).toHaveLength(2);
    expect(outputs[0]?.data).toEqual({ 'text/plain': '50/50' });
  });
});
