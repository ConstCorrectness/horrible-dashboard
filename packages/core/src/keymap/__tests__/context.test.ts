import { describe, expect, it } from 'vitest';

import {
  CONTEXT_KEYS,
  evaluateWhen,
  keysUsed,
  testWhen,
  validateWhen,
  WhenError,
  type KeyContext,
} from '../context';

function ctx(over: Partial<KeyContext> = {}): KeyContext {
  return {
    paneFocus: null,
    paneInstance: null,
    capture: null,
    captureView: null,
    textInput: false,
    dialogOpen: false,
    fullscreenArea: false,
    windowFocused: false,
    desktopMode: 'tiling',
    shellView: 'desktop',
    platform: 'win',
    host: 'browser',
    ...over,
  };
}

describe('evaluateWhen', () => {
  it('compares a string key', () => {
    const where = ctx({ paneFocus: 'editor.buffer' });
    expect(evaluateWhen("paneFocus == 'editor.buffer'", where)).toBe(true);
    expect(evaluateWhen("paneFocus == 'terminal.instance'", where)).toBe(false);
    expect(evaluateWhen("paneFocus != 'terminal.instance'", where)).toBe(true);
  });

  it('treats a bare key as a truthiness test', () => {
    expect(evaluateWhen('textInput', ctx({ textInput: true }))).toBe(true);
    expect(evaluateWhen('textInput', ctx())).toBe(false);
    expect(evaluateWhen('paneFocus', ctx({ paneFocus: 'x' }))).toBe(true);
    expect(evaluateWhen('paneFocus', ctx())).toBe(false);
  });

  it('handles !, && and || with the expected precedence', () => {
    const where = ctx({ paneFocus: 'editor.buffer', dialogOpen: true });
    expect(evaluateWhen('!dialogOpen', where)).toBe(false);
    expect(evaluateWhen("paneFocus == 'editor.buffer' && !dialogOpen", where)).toBe(false);
    expect(evaluateWhen("paneFocus == 'editor.buffer' || dialogOpen", where)).toBe(true);
    // && binds tighter than ||
    expect(evaluateWhen('dialogOpen || textInput && fullscreenArea', where)).toBe(true);
    expect(evaluateWhen('(dialogOpen || textInput) && fullscreenArea', where)).toBe(false);
  });

  it('compares against null for an unset key', () => {
    expect(evaluateWhen('capture == null', ctx())).toBe(true);
    expect(evaluateWhen('capture == null', ctx({ capture: 'full' }))).toBe(false);
    expect(evaluateWhen("capture == 'full'", ctx({ capture: 'full' }))).toBe(true);
  });

  it('covers every declared context key', () => {
    const where = ctx({
      paneFocus: 'a',
      paneInstance: 'a#1',
      capture: 'full',
      captureView: 'a',
      textInput: true,
      dialogOpen: true,
      fullscreenArea: true,
    });
    for (const { key } of CONTEXT_KEYS) {
      expect(() => evaluateWhen(String(key), where)).not.toThrow();
    }
  });

  it('rejects a key outside the closed vocabulary', () => {
    // The whole point of the closed set: the agent cannot invent a context key
    // that silently never matches.
    expect(() => evaluateWhen('editorTextFocus', ctx())).toThrow(WhenError);
    expect(validateWhen('editorTextFocus').ok).toBe(false);
    expect(validateWhen("paneFocus == 'x'").ok).toBe(true);
  });

  it('rejects malformed syntax', () => {
    expect(() => evaluateWhen("paneFocus == 'x'  ||", ctx())).toThrow(WhenError);
    expect(() => evaluateWhen("(paneFocus == 'x'", ctx())).toThrow(WhenError);
  });
});

describe('testWhen', () => {
  it('an absent clause always matches', () => {
    expect(testWhen(undefined, ctx())).toBe(true);
  });

  it('a malformed clause never matches, rather than throwing into the key handler', () => {
    expect(testWhen('bogusKey', ctx())).toBe(false);
  });
});

describe('keysUsed', () => {
  it('collects the keys a clause names', () => {
    expect([...keysUsed("paneFocus == 'x' && !dialogOpen")].sort()).toEqual([
      'dialogOpen',
      'paneFocus',
    ]);
  });

  it('returns nothing for a malformed clause', () => {
    expect(keysUsed('nope ==').size).toBe(0);
  });
});
