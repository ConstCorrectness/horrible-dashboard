import { EditorState } from '@codemirror/state';
import { describe, expect, it } from 'vitest';

import { importContextAt } from '../importContext';

/** Classify the cursor at the `|` in `doc`. */
function at(doc: string) {
  const pos = doc.indexOf('|');
  const state = EditorState.create({ doc: doc.replace('|', '') });
  return importContextAt(state, pos);
}

describe('importContextAt', () => {
  it('recognises a module position', () => {
    expect(at('from |')).toEqual({ kind: 'module', from: 5, prefix: '' });
    expect(at('from vll|')).toEqual({ kind: 'module', from: 5, prefix: 'vll' });
    expect(at('import |')).toEqual({ kind: 'module', from: 7, prefix: '' });
    expect(at('from vllm.lora.|')).toMatchObject({ kind: 'module', prefix: 'vllm.lora.' });
    expect(at('    from os|')).toMatchObject({ kind: 'module', prefix: 'os' });
  });

  it('recognises a member position, carrying the module', () => {
    expect(at('from vllm import |')).toEqual({
      kind: 'member',
      module: 'vllm',
      from: 17,
      prefix: '',
    });
    expect(at('from vllm import L|')).toMatchObject({
      kind: 'member',
      module: 'vllm',
      prefix: 'L',
    });
    // A continuation of the list is still a member position for the same module.
    expect(at('from vllm import LLM, Sam|')).toMatchObject({
      kind: 'member',
      module: 'vllm',
      prefix: 'Sam',
    });
    expect(at('from vllm import (|')).toMatchObject({ kind: 'member', module: 'vllm', prefix: '' });
  });

  it('anchors `from` at the start of the typed text, not at the cursor', () => {
    const ctx = at('from vll|');
    expect(ctx?.from).toBe('from '.length);
  });

  it('declines everywhere else', () => {
    expect(at('x = |')).toBeNull();
    expect(at('def f(|')).toBeNull();
    expect(at('|')).toBeNull();
    // Only the text before the cursor on this line is read, so a completed import
    // earlier in the buffer cannot leak into an unrelated position.
    expect(at('import os\nprint(|')).toBeNull();
    // `import x as y` is a rename, not a completable module position.
    expect(at('import os as |')).toBeNull();
  });
});
