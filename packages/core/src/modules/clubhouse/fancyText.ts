/**
 * "Fancy text": restyling a string by swapping its letters for Unicode lookalikes.
 *
 * Clubhouse sends plain strings — there is no formatting on the wire — so the only
 * styling that survives the trip is a different character. Most styles come from the
 * Mathematical Alphanumeric Symbols block (U+1D400…), which spells each alphabet as a
 * contiguous run of 26 capitals then 26 lowercase. A few letters were already encoded
 * earlier in Letterlike Symbols (ℎ, ℬ, ℭ, ℝ …), so the block leaves *holes* there —
 * reserved, unassigned code points that render as boxes. Each style lists its holes and
 * the letter that fills them; getting one wrong is a silent tofu square in someone
 * else's room.
 *
 * Every converter passes anything it has no glyph for (punctuation, emoji, accented
 * letters) through untouched, and starts by folding already-styled text back to ASCII,
 * so switching style re-styles the text instead of stacking two styles.
 */

import { plainName, toBadgeLetters } from './badgeLetters';

export type FancyStyle =
  | 'plain'
  | 'bold'
  | 'italic'
  | 'boldItalic'
  | 'script'
  | 'boldScript'
  | 'fraktur'
  | 'doubleStruck'
  | 'sansBold'
  | 'monospace'
  | 'wide'
  | 'bubble'
  | 'badge';

interface AlphabetStyle {
  upper: number;
  lower: number;
  digits?: number;
  /** Letters the math block leaves as holes, filled from Letterlike Symbols. */
  holes?: Record<string, number>;
}

const ALPHABETS: Partial<Record<FancyStyle, AlphabetStyle>> = {
  bold: { upper: 0x1d400, lower: 0x1d41a, digits: 0x1d7ce },
  italic: { upper: 0x1d434, lower: 0x1d44e, holes: { h: 0x210e } },
  boldItalic: { upper: 0x1d468, lower: 0x1d482 },
  script: {
    upper: 0x1d49c,
    lower: 0x1d4b6,
    holes: {
      B: 0x212c,
      E: 0x2130,
      F: 0x2131,
      H: 0x210b,
      I: 0x2110,
      L: 0x2112,
      M: 0x2133,
      R: 0x211b,
      e: 0x212f,
      g: 0x210a,
      o: 0x2134,
    },
  },
  boldScript: { upper: 0x1d4d0, lower: 0x1d4ea },
  fraktur: {
    upper: 0x1d504,
    lower: 0x1d51e,
    holes: { C: 0x212d, H: 0x210c, I: 0x2111, R: 0x211c, Z: 0x2128 },
  },
  doubleStruck: {
    upper: 0x1d538,
    lower: 0x1d552,
    digits: 0x1d7d8,
    holes: { C: 0x2102, H: 0x210d, N: 0x2115, P: 0x2119, Q: 0x211a, R: 0x211d, Z: 0x2124 },
  },
  sansBold: { upper: 0x1d5d4, lower: 0x1d5ee, digits: 0x1d7ec },
  monospace: { upper: 0x1d670, lower: 0x1d68a, digits: 0x1d7f6 },
};

const STYLE_NAMES: Record<FancyStyle, string> = {
  plain: 'Plain',
  bold: 'Bold',
  italic: 'Italic',
  boldItalic: 'Bold italic',
  script: 'Script',
  boldScript: 'Bold script',
  fraktur: 'Fraktur',
  doubleStruck: 'Double struck',
  sansBold: 'Sans bold',
  monospace: 'Monospace',
  wide: 'Wide',
  bubble: 'Bubble',
  badge: 'Badge',
};

const FULLWIDTH_OFFSET = 0xfee0; // '!'..'~' -> U+FF01..U+FF5E
const IDEOGRAPHIC_SPACE = 0x3000;
const CIRCLED_UPPER = 0x24b6;
const CIRCLED_LOWER = 0x24d0;
const CIRCLED_ONE = 0x2460; // ① .. ⑨
const CIRCLED_ZERO = 0x24ea;

const isUpper = (c: number) => c >= 0x41 && c <= 0x5a;
const isLower = (c: number) => c >= 0x61 && c <= 0x7a;
const isDigit = (c: number) => c >= 0x30 && c <= 0x39;

/** Every styled code point back to its ASCII letter/digit/space — the inverse map. */
const TO_ASCII: Map<number, string> = (() => {
  const map = new Map<number, string>();
  for (const style of Object.values(ALPHABETS)) {
    for (let i = 0; i < 26; i++) {
      const U = String.fromCharCode(0x41 + i);
      const l = String.fromCharCode(0x61 + i);
      map.set(style.holes?.[U] ?? style.upper + i, U);
      map.set(style.holes?.[l] ?? style.lower + i, l);
    }
    if (style.digits != null) {
      for (let i = 0; i < 10; i++) map.set(style.digits + i, String(i));
    }
  }
  for (let c = 0x21; c <= 0x7e; c++) map.set(c + FULLWIDTH_OFFSET, String.fromCharCode(c));
  map.set(IDEOGRAPHIC_SPACE, ' ');
  for (let i = 0; i < 26; i++) {
    map.set(CIRCLED_UPPER + i, String.fromCharCode(0x41 + i));
    map.set(CIRCLED_LOWER + i, String.fromCharCode(0x61 + i));
  }
  for (let i = 1; i <= 9; i++) map.set(CIRCLED_ONE + i - 1, String(i));
  map.set(CIRCLED_ZERO, '0');
  return map;
})();

/** Fold any styled text (including badge letters) back to plain ASCII where it can. */
export function unstylize(text: string): string {
  let out = '';
  for (const ch of plainName(text)) out += TO_ASCII.get(ch.codePointAt(0)!) ?? ch;
  return out;
}

function mapChars(text: string, convert: (c: number) => number | null): string {
  let out = '';
  for (const ch of text) {
    const cp = ch.codePointAt(0)!;
    const next = convert(cp);
    out += next == null ? ch : String.fromCodePoint(next);
  }
  return out;
}

/** `text` re-spelled in `style`. Characters a style has no glyph for pass through. */
export function stylize(text: string, style: FancyStyle): string {
  const plain = unstylize(text);
  if (style === 'plain') return plain;
  if (style === 'badge') return toBadgeLetters(plain);
  if (style === 'wide') {
    return mapChars(plain, (c) =>
      c === 0x20 ? IDEOGRAPHIC_SPACE : c >= 0x21 && c <= 0x7e ? c + FULLWIDTH_OFFSET : null,
    );
  }
  if (style === 'bubble') {
    return mapChars(plain, (c) => {
      if (isUpper(c)) return CIRCLED_UPPER + c - 0x41;
      if (isLower(c)) return CIRCLED_LOWER + c - 0x61;
      if (c === 0x30) return CIRCLED_ZERO;
      if (isDigit(c)) return CIRCLED_ONE + c - 0x31;
      return null;
    });
  }
  const a = ALPHABETS[style]!;
  return mapChars(plain, (c) => {
    const hole = a.holes?.[String.fromCharCode(c)];
    if (hole != null && (isUpper(c) || isLower(c))) return hole;
    if (isUpper(c)) return a.upper + c - 0x41;
    if (isLower(c)) return a.lower + c - 0x61;
    if (isDigit(c) && a.digits != null) return a.digits + c - 0x30;
    return null;
  });
}

/** Picker order; each label is spelled in its own style so the menu is the preview. */
export const FANCY_STYLES: { id: FancyStyle; label: string }[] = (
  Object.keys(STYLE_NAMES) as FancyStyle[]
).map((id) => ({ id, label: stylize(STYLE_NAMES[id], id) }));
