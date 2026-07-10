import { describe, expect, it } from 'vitest';

import { matchesKeySpec, resolveKeybinding } from '../../keybindings';

function key(
  k: string,
  mods: Partial<Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>> = {},
): KeyboardEvent {
  return {
    key: k,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...mods,
  } as KeyboardEvent;
}

describe('matchesKeySpec — legacy specs (behavior preserved)', () => {
  it('matches mod+letter with ctrl or meta', () => {
    expect(matchesKeySpec(key('k', { ctrlKey: true }), 'mod+k')).toBe(true);
    expect(matchesKeySpec(key('k', { metaKey: true }), 'mod+k')).toBe(true);
    expect(matchesKeySpec(key('k'), 'mod+k')).toBe(false);
  });

  it('plain letters match without modifiers only', () => {
    expect(matchesKeySpec(key('t'), 't')).toBe(true);
    expect(matchesKeySpec(key('t', { ctrlKey: true }), 't')).toBe(false);
    expect(matchesKeySpec(key('t', { altKey: true }), 't')).toBe(false);
  });

  it('mod specs reject extra alt (so mod+b and mod+alt+b stay distinct)', () => {
    expect(matchesKeySpec(key('b', { ctrlKey: true, altKey: true }), 'mod+b')).toBe(false);
  });
});

describe('matchesKeySpec — multi-modifier specs', () => {
  it('matches mod+alt+letter', () => {
    expect(matchesKeySpec(key('b', { ctrlKey: true, altKey: true }), 'mod+alt+b')).toBe(true);
    expect(matchesKeySpec(key('b', { ctrlKey: true }), 'mod+alt+b')).toBe(false);
  });

  it('matches alt+arrow and alt+shift+arrow distinctly', () => {
    expect(matchesKeySpec(key('ArrowLeft', { altKey: true }), 'alt+left')).toBe(true);
    expect(matchesKeySpec(key('ArrowLeft', { altKey: true, shiftKey: true }), 'alt+left')).toBe(
      false,
    );
    expect(
      matchesKeySpec(key('ArrowLeft', { altKey: true, shiftKey: true }), 'alt+shift+left'),
    ).toBe(true);
  });

  it('matches ctrl+space literally (not meta)', () => {
    expect(matchesKeySpec(key(' ', { ctrlKey: true }), 'ctrl+space')).toBe(true);
    expect(matchesKeySpec(key(' ', { metaKey: true }), 'ctrl+space')).toBe(false);
  });

  it('accepts arrow aliases in both spellings', () => {
    expect(matchesKeySpec(key('ArrowDown', { altKey: true }), 'alt+down')).toBe(true);
    expect(matchesKeySpec(key('ArrowDown', { altKey: true }), 'alt+arrowdown')).toBe(true);
  });

  it('matches digits with mod', () => {
    expect(matchesKeySpec(key('1', { ctrlKey: true }), 'mod+1')).toBe(true);
  });
});

describe('resolveKeybinding precedence (unchanged)', () => {
  const bindings = [
    { key: 'n', command: 'region.toggle:right:editor.buffer', scope: 'editor.buffer' },
    { key: 'mod+k', command: 'shell.commandPalette', override: true },
    { key: 'n', command: 'global.n' },
  ];

  it('scoped beats plain global; override-global beats scoped', () => {
    expect(resolveKeybinding(key('n'), 'editor.buffer', bindings)).toBe(
      'region.toggle:right:editor.buffer',
    );
    expect(resolveKeybinding(key('n'), null, bindings)).toBe('global.n');
    expect(resolveKeybinding(key('k', { ctrlKey: true }), 'editor.buffer', bindings)).toBe(
      'shell.commandPalette',
    );
  });
});
