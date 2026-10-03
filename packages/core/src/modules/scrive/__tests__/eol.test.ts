import { describe, expect, it } from 'vitest';

import { detectEol } from '../eol';

describe('detectEol', () => {
  it('reads LF and CRLF files as what they are', () => {
    expect(detectEol('a\nb\n')).toBe('\n');
    expect(detectEol('a\r\nb\r\n')).toBe('\r\n');
  });

  it('lets the majority win in a mixed file', () => {
    expect(detectEol('a\r\nb\r\nc\n')).toBe('\r\n');
    expect(detectEol('a\nb\nc\r\n')).toBe('\n');
  });

  it('treats a one-line file as LF', () => {
    expect(detectEol('no newline')).toBe('\n');
  });
});
