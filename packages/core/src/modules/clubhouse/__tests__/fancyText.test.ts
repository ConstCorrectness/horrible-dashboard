import { describe, expect, it } from 'vitest';

import { FANCY_STYLES, stylize, unstylize, type FancyStyle } from '../fancyText';
import { normalizeCustomReaction } from '../reactions';

const ALL = FANCY_STYLES.map((s) => s.id);
const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';

describe('fancy text', () => {
  it('spells the common styles', () => {
    expect(stylize('Hi 5', 'bold')).toBe('𝐇𝐢 𝟓');
    expect(stylize('beautiful', 'boldScript')).toBe('𝓫𝓮𝓪𝓾𝓽𝓲𝓯𝓾𝓵');
    expect(stylize('gg', 'wide')).toBe('ｇｇ');
    expect(stylize('a b', 'wide')).toBe('ａ　ｂ');
    expect(stylize('ok 10', 'bubble')).toBe('ⓞⓚ ①⓪');
    expect(stylize('lol', 'monospace')).toBe('𝚕𝚘𝚕');
  });

  it('fills the holes the math block leaves with Letterlike Symbols', () => {
    expect(stylize('h', 'italic')).toBe('ℎ');
    expect(stylize('BEe', 'script')).toBe('ℬℰℯ');
    expect(stylize('CHIRZ', 'fraktur')).toBe('ℭℌℑℜℨ');
    expect(stylize('CR', 'doubleStruck')).toBe('ℂℝ');
  });

  it('never emits a reserved (unassigned) code point', () => {
    // A hole left unfilled is \p{Cn}: it renders as a box on every platform.
    for (const style of ALL) {
      const out = stylize(ALPHABET + '0123456789', style);
      expect(/\p{Cn}/u.test(out), style).toBe(false);
    }
  });

  it('round-trips every style back to plain, so switching never stacks', () => {
    for (const style of ALL) {
      const lettersOnly = style === 'badge' ? 'HELLO' : 'Hello World 42';
      expect(unstylize(stylize(lettersOnly, style)), style).toBe(lettersOnly);
    }
    expect(stylize(stylize('swap', 'fraktur'), 'bold')).toBe(stylize('swap', 'bold'));
  });

  it('passes through what a style has no glyph for', () => {
    expect(stylize('é!? 🎉', 'bold')).toBe('é!? 🎉');
    expect(stylize('7', 'italic')).toBe('7');
  });

  it('produces reactions the backend accepts', () => {
    for (const style of ALL as FancyStyle[]) {
      expect(normalizeCustomReaction(stylize('nice one', style)), style).not.toBeNull();
    }
  });

  it('labels each style in its own style', () => {
    expect(FANCY_STYLES[0]).toEqual({ id: 'plain', label: 'Plain' });
    expect(FANCY_STYLES.find((s) => s.id === 'bold')?.label).toBe('𝐁𝐨𝐥𝐝');
  });
});
