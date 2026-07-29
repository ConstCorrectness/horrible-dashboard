import { describe, expect, it } from 'vitest';

import {
  formatSpec,
  KeySpecError,
  labelSpec,
  matchesKeySpec,
  parseSpec,
  specsFromEvent,
  tryParseSpec,
} from '../spec';

function key(
  k: string,
  mods: Partial<Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>> = {},
  code = '',
): KeyboardEvent {
  return {
    key: k,
    code,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...mods,
  } as KeyboardEvent;
}

describe('parseSpec', () => {
  it('parses modifiers and the key', () => {
    expect(parseSpec('mod+k')).toEqual([
      { mod: true, ctrl: false, meta: false, alt: false, shift: false, kind: 'key', value: 'k' },
    ]);
  });

  it('accepts modifier synonyms', () => {
    expect(parseSpec('cmd+option+p')[0]).toMatchObject({ meta: true, alt: true, value: 'p' });
    expect(parseSpec('control+x')[0]).toMatchObject({ ctrl: true, value: 'x' });
  });

  it('parses a multi-stroke sequence', () => {
    const chord = parseSpec('mod+k mod+s');
    expect(chord).toHaveLength(2);
    expect(chord[1]).toMatchObject({ mod: true, value: 's' });
  });

  it('treats a trailing + as the plus key', () => {
    expect(parseSpec('mod++')[0]).toMatchObject({ mod: true, value: '+' });
  });

  it('rejects an unknown modifier and a redundant mod', () => {
    expect(() => parseSpec('hyper+k')).toThrow(KeySpecError);
    expect(() => parseSpec('mod+ctrl+k')).toThrow(KeySpecError);
    expect(tryParseSpec('hyper+k')).toBeNull();
  });

  it('round-trips through formatSpec', () => {
    for (const spec of [
      'mod+k',
      'alt+shift+arrowleft',
      'code:KeyW',
      'mod+k mod+s',
      // The space key's e.key is a literal ' ', which would re-parse as `+`
      // (strokes are space-separated) — it has to come back out as `space`.
      'ctrl+space',
      'mod++',
    ]) {
      expect(formatSpec(parseSpec(spec))).toBe(spec);
    }
  });
});

describe('the two key identities', () => {
  it('matches a bare token against e.key', () => {
    expect(matchesKeySpec(key('w', {}, 'KeyW'), 'w')).toBe(true);
    // A French AZERTY 'w' sits on the physical Z key: the character still matches.
    expect(matchesKeySpec(key('w', {}, 'KeyZ'), 'w')).toBe(true);
  });

  it('matches a code: token against e.code, ignoring the character', () => {
    expect(matchesKeySpec(key('z', {}, 'KeyW'), 'code:KeyW')).toBe(true);
    expect(matchesKeySpec(key('w', {}, 'KeyZ'), 'code:KeyW')).toBe(false);
  });

  it('promotes shift+digit to positional matching', () => {
    // The whole point: the browser reports e.key '!' for shift+1, so a character
    // spec could never fire. `mod+shift+1` has to compare Digit1.
    const parsed = parseSpec('mod+shift+1')[0];
    expect(parsed).toMatchObject({ kind: 'code', value: 'Digit1' });
    expect(
      matchesKeySpec(key('!', { ctrlKey: true, shiftKey: true }, 'Digit1'), 'mod+shift+1'),
    ).toBe(true);
  });

  it('promotes shift+punctuation too', () => {
    expect(parseSpec('shift+,')[0]).toMatchObject({ kind: 'code', value: 'Comma' });
  });

  it('leaves an unshifted digit on the character path', () => {
    expect(parseSpec('mod+1')[0]).toMatchObject({ kind: 'key', value: '1' });
    expect(matchesKeySpec(key('1', { ctrlKey: true }, 'Digit1'), 'mod+1')).toBe(true);
  });
});

describe('matchesKeySpec — behavior carried over from the old service', () => {
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

  it('modifiers match exactly, so mod+b and mod+alt+b stay distinct', () => {
    expect(matchesKeySpec(key('b', { ctrlKey: true, altKey: true }), 'mod+b')).toBe(false);
    expect(matchesKeySpec(key('b', { ctrlKey: true, altKey: true }), 'mod+alt+b')).toBe(true);
  });

  it('matches ctrl+space literally, not meta', () => {
    expect(matchesKeySpec(key(' ', { ctrlKey: true }), 'ctrl+space')).toBe(true);
    expect(matchesKeySpec(key(' ', { metaKey: true }), 'ctrl+space')).toBe(false);
  });

  it('accepts arrow aliases in both spellings', () => {
    expect(matchesKeySpec(key('ArrowDown', { altKey: true }), 'alt+down')).toBe(true);
    expect(matchesKeySpec(key('ArrowDown', { altKey: true }), 'alt+arrowdown')).toBe(true);
    expect(matchesKeySpec(key('ArrowLeft', { altKey: true, shiftKey: true }), 'alt+left')).toBe(
      false,
    );
  });
});

describe('labelSpec', () => {
  it('uses symbols on mac and words elsewhere', () => {
    expect(labelSpec(parseSpec('mod+k'), { platform: 'mac' })).toBe('⌘K');
    expect(labelSpec(parseSpec('mod+k'), { platform: 'win' })).toBe('Ctrl+K');
    expect(labelSpec(parseSpec('alt+shift+left'), { platform: 'win' })).toBe('Alt+Shift+←');
  });

  it('labels a code: spec from the layout map when one is available', () => {
    const layoutMap = new Map([['KeyW', 'z']]);
    expect(labelSpec(parseSpec('code:KeyW'), { platform: 'win', layoutMap })).toBe('Z');
    // Without a layout map it falls back to the US-layout character.
    expect(labelSpec(parseSpec('code:KeyW'), { platform: 'win' })).toBe('W');
  });

  it('joins a sequence with a space', () => {
    expect(labelSpec(parseSpec('mod+k mod+s'), { platform: 'win' })).toBe('Ctrl+K Ctrl+S');
  });
});

describe('specsFromEvent', () => {
  it('offers both spellings of the same keystroke', () => {
    expect(specsFromEvent(key('w', { ctrlKey: true }, 'KeyW'))).toEqual({
      key: 'mod+w',
      code: 'mod+code:KeyW',
    });
  });

  it('spells arrows with the friendly alias', () => {
    expect(specsFromEvent(key('ArrowLeft', { altKey: true }, 'ArrowLeft'))?.key).toBe('alt+left');
  });

  it('returns null for a bare modifier press', () => {
    expect(specsFromEvent(key('Shift', { shiftKey: true }, 'ShiftLeft'))).toBeNull();
  });
});
