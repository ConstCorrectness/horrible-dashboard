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
    expect(at('from |')).toMatchObject({ kind: 'module', from: 5, prefix: '' });
    expect(at('from vll|')).toMatchObject({ kind: 'module', from: 5, prefix: 'vll' });
    expect(at('import |')).toMatchObject({ kind: 'module', from: 7, prefix: '' });
    expect(at('from vllm.lora.|')).toMatchObject({ kind: 'module', prefix: 'vllm.lora.' });
    expect(at('    from os|')).toMatchObject({ kind: 'module', prefix: 'os' });
  });

  it('recognises a member position, carrying the module', () => {
    expect(at('from vllm import |')).toMatchObject({
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

describe('justOpened — when the popup may open on its own', () => {
  /**
   * The empty-prefix cases are the whole reason this module exists, and they are
   * also the ones nothing can auto-trigger on: there is no word to match. `explicit`
   * (Tab, or the toggle chord) always answers them. `justOpened` is the one place
   * they are answered *without* being asked — the instant after the keyword, which
   * is where VS Code opens its suggest widget and where the user reaches for it.
   */
  it('is set directly after the keyword and one space', () => {
    expect(at('import |')?.justOpened).toBe(true);
    expect(at('from |')?.justOpened).toBe(true);
    expect(at('from transformers import |')?.justOpened).toBe(true);
  });

  it('is set after a comma in an import list', () => {
    expect(at('from vllm import LLM, |')?.justOpened).toBe(true);
    expect(at('from vllm import (|')?.justOpened).toBe(true);
  });

  it('is not set on a second space', () => {
    // Two spaces is not a person waiting to be told the options; opening on every
    // space in the statement is what the empty-prefix guard originally prevented.
    expect(at('from transformers import  |')?.justOpened).toBe(false);
    expect(at('import  |')?.justOpened).toBe(false);
  });

  it('is not set while a name is being typed', () => {
    // A prefix is present, so the ordinary sources answer anyway — but the flag
    // must not claim this is the "just opened" moment.
    expect(at('from transformers import Auto|')?.justOpened).toBe(false);
    expect(at('import num|')?.justOpened).toBe(false);
  });

  it('does not fire on a word merely ending in `import`', () => {
    expect(at('reimport |')).toBeNull();
  });
});
