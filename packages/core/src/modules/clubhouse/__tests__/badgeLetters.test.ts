import { describe, expect, it } from 'vitest';

import {
  BADGE_SEPARATOR,
  firstName,
  hasBadgeLetters,
  nameInitials,
  parseBadgeName,
  plainName,
  toBadgeLetters,
  toBadgeLettersInRange,
} from '../badgeLetters';

const ri = (letter: string) => String.fromCodePoint(0x1f1e6 + letter.charCodeAt(0) - 0x41);
const PROBIN = ['P', 'R', 'O', 'B', 'I', 'N'].map(ri).join(BADGE_SEPARATOR);

describe('spelling a name in badge letters', () => {
  it('fences every letter, so PR and IN do not become Puerto Rico and India', () => {
    expect(toBadgeLetters('probin')).toBe(PROBIN);
  });

  it('leaves spaces, digits and punctuation as they are', () => {
    expect(toBadgeLetters('Horrible 2!')).toBe(
      ['H', 'O', 'R', 'R', 'I', 'B', 'L', 'E'].map(ri).join(BADGE_SEPARATOR) + ' 2!',
    );
  });

  it('is idempotent', () => {
    expect(toBadgeLetters(PROBIN)).toBe(PROBIN);
    expect(toBadgeLetters(`Horrible ${PROBIN}`)).toBe(toBadgeLetters('Horrible PROBIN'));
  });

  it('keeps a real flag whole and fences it from a neighbouring letter', () => {
    const us = ri('U') + ri('S');
    // Without the fence, A + U would pair into Australia and orphan the S.
    expect(toBadgeLetters(`a${us}`)).toBe(ri('A') + BADGE_SEPARATOR + us);
    expect(parseBadgeName(toBadgeLetters(`a${us}b`))).toEqual([
      { kind: 'letters', text: 'A' },
      { kind: 'text', text: us + BADGE_SEPARATOR },
      { kind: 'letters', text: 'B' },
    ]);
  });
});

describe('badging part of a name', () => {
  it('converts only the selected range', () => {
    expect(toBadgeLettersInRange('Horrible probin', 9, 15)).toBe(`Horrible ${PROBIN}`);
  });

  it('fences new letters from badge letters already at either edge', () => {
    const probi = ['P', 'R', 'O', 'B', 'I'].map(ri).join(BADGE_SEPARATOR);
    expect(toBadgeLettersInRange(`${probi}n`, probi.length, probi.length + 1)).toBe(PROBIN);
    expect(plainName(toBadgeLettersInRange(`p${ri('R')}`, 0, 1))).toBe('PR');
  });

  it('converts everything when nothing is selected', () => {
    expect(toBadgeLettersInRange('probin', 3, 3)).toBe(PROBIN);
  });
});

describe('reading a name back', () => {
  it('decodes fenced indicators into one letter run', () => {
    expect(parseBadgeName(`Horrible ${PROBIN}`)).toEqual([
      { kind: 'text', text: 'Horrible ' },
      { kind: 'letters', text: 'PROBIN' },
    ]);
    expect(plainName(PROBIN)).toBe('PROBIN');
    expect(hasBadgeLetters(PROBIN)).toBe(true);
  });

  it('treats an unfenced pair as the flag a phone would draw', () => {
    const india = ri('I') + ri('N');
    expect(parseBadgeName(`Raj ${india}`)).toEqual([{ kind: 'text', text: `Raj ${india}` }]);
    expect(hasBadgeLetters(`Raj ${india}`)).toBe(false);
  });

  it('reads the odd one out of an unfenced run as a letter', () => {
    expect(parseBadgeName(ri('I') + ri('N') + ri('X'))).toEqual([
      { kind: 'text', text: ri('I') + ri('N') },
      { kind: 'letters', text: 'X' },
    ]);
  });

  it('accepts the other zero-width fences people use', () => {
    expect(plainName([ri('A'), ri('B')].join('‌'))).toBe('AB');
    expect(plainName([ri('A'), ri('B')].join('⁠'))).toBe('AB');
  });

  it('leaves ordinary names alone', () => {
    expect(parseBadgeName('Jennifer')).toEqual([{ kind: 'text', text: 'Jennifer' }]);
  });
});

describe('tile labels', () => {
  it('does not split a badge name at its zero-width fences', () => {
    expect(plainName(firstName(PROBIN))).toBe('PROBIN');
    expect(firstName('James Smith')).toBe('James');
  });

  it('takes initials off the plain name, never half a surrogate pair', () => {
    expect(nameInitials(PROBIN)).toBe('P');
    expect(nameInitials(`Horrible ${PROBIN}`)).toBe('HP');
    expect(nameInitials('🐻 anon')).toBe('🐻a');
    expect(nameInitials('')).toBe('');
  });
});
