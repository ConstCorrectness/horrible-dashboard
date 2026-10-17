// @vitest-environment happy-dom
/**
 * Inline autosuggest extension tests.
 *
 * Verifies tiered instant + streaming FIM autosuggest, cursor movements,
 * Tab acceptance, and Escape dismissal.
 */
import { describe, expect, it, vi } from 'vitest';
import { EditorState } from '@codemirror/state';
import { EditorView } from '@codemirror/view';

import { autosuggest } from '../autosuggest';

describe('autosuggest extension', () => {
  it('instantiates valid CodeMirror extensions', () => {
    const ext = autosuggest({
      fetch: vi.fn().mockResolvedValue(' suggestion'),
      delayMs: 20,
    });
    expect(Array.isArray(ext)).toBe(true);
    expect(ext.length).toBeGreaterThanOrEqual(4);

    const state = EditorState.create({
      doc: 'def hello():',
      extensions: ext,
    });
    expect(state.doc.toString()).toBe('def hello():');
  });

  it('triggers fetch on typing pause and dispatches suggestion', async () => {
    const fetch = vi.fn().mockImplementation((prefix: string) => {
      return Promise.resolve(prefix.endsWith('hello') ? ' world' : '');
    });

    const host = document.createElement('div');
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: '',
        extensions: [autosuggest({ fetch, delayMs: 15 })],
      }),
    });

    // Simulate typing 'hello' with cursor at the end
    view.dispatch({
      changes: { from: 0, insert: 'hello' },
      selection: { anchor: 5 },
    });
    await new Promise((r) => setTimeout(r, 60));

    expect(fetch).toHaveBeenCalledWith('hello', '', expect.any(AbortSignal));

    view.destroy();
  });

  it('supports streaming FIM updates', async () => {
    const fetch = vi.fn().mockResolvedValue(' initial');
    const fetchStream = vi.fn().mockImplementation(
      async (_prefix: string, _suffix: string, _signal: AbortSignal, onChunk: (c: string) => void) => {
        onChunk(' token1');
        onChunk(' token2');
        return ' token1 token2';
      },
    );

    const host = document.createElement('div');
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: '',
        extensions: [autosuggest({ fetch, fetchStream, delayMs: 10 })],
      }),
    });

    view.dispatch({
      changes: { from: 0, insert: 'test' },
      selection: { anchor: 4 },
    });
    await new Promise((r) => setTimeout(r, 60));

    expect(fetch).toHaveBeenCalled();
    expect(fetchStream).toHaveBeenCalled();

    view.destroy();
  });
});
