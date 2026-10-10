/**
 * "Badge letters": the blue spaced capitals some Clubhouse names show on phones.
 *
 * They are not a Clubhouse feature — nothing in the profile payload carries a
 * style. The name is spelled in Unicode *regional indicator symbols*
 * (U+1F1E6–U+1F1FF, the code points flag emoji are built from). Two adjacent
 * indicators pair into a flag (🇮 + 🇳 = India), so each letter is fenced off with
 * a zero-width space; a lone indicator has no flag to become, and the phone's
 * emoji font draws it as a letter — Samsung's as a bold blue capital, which is
 * the look in question. Unicode's grapheme rules pair indicators left to right
 * and a zero-width space breaks the pair, so fencing every letter is enough.
 *
 * Desktop emoji fonts draw lone indicators inconsistently (Segoe UI Emoji as a
 * boxed letter), so the pane decodes them back to ASCII and styles the run
 * itself rather than trusting the platform glyph.
 */

const RI_FIRST = 0x1f1e6;
const RI_LAST = 0x1f1ff;

/** Between every pair of letters; anything wider would show as a gap. */
export const BADGE_SEPARATOR = '​';

/** Invisible characters people use as the fence. We emit only ZWSP. */
const FENCES = new Set(['​', '‌', '⁠']);

const isIndicator = (cp: number) => cp >= RI_FIRST && cp <= RI_LAST;

export type NameSegment = { kind: 'text'; text: string } | { kind: 'letters'; text: string };

/**
 * Split a display name into plain text and badge-letter runs.
 *
 * Indicators are paired exactly as a phone pairs them: within an unfenced run they
 * pair left to right into flags, which stay as text (a real 🇺🇸 is a flag, not
 * the letters US); only an unpaired indicator is a letter. Fences between letters
 * are dropped, and a fence that separates nothing is kept as text.
 */
export function parseBadgeName(name: string): NameSegment[] {
  const out: NameSegment[] = [];
  const push = (kind: NameSegment['kind'], text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ kind, text });
  };

  const chars = Array.from(name);
  let i = 0;
  while (i < chars.length) {
    const cp = chars[i].codePointAt(0)!;
    if (!isIndicator(cp)) {
      // A fence counts as part of the letter run only when letters sit on both
      // sides of it; the lookahead below consumes those, so here it is plain text.
      push('text', chars[i]);
      i += 1;
      continue;
    }
    // One unfenced run of indicators: pair it off, the odd one out is a letter.
    let j = i;
    while (j < chars.length && isIndicator(chars[j].codePointAt(0)!)) j += 1;
    const run = chars.slice(i, j);
    for (let k = 0; k + 1 < run.length; k += 2) push('text', run[k] + run[k + 1]);
    if (run.length % 2 === 1) {
      const letter = String.fromCharCode(0x41 + run[run.length - 1].codePointAt(0)! - RI_FIRST);
      push('letters', letter);
      // Swallow fences that lead straight into another indicator.
      let f = j;
      while (f < chars.length && FENCES.has(chars[f])) f += 1;
      if (f > j && f < chars.length && isIndicator(chars[f].codePointAt(0)!)) j = f;
    }
    i = j;
  }
  return out;
}

/** The name as a person would read it aloud: badge letters become ASCII. */
export function plainName(name: string): string {
  return parseBadgeName(name)
    .map((s) => s.text)
    .join('');
}

/** True when the name carries at least one badge-letter run. */
export function hasBadgeLetters(name: string): boolean {
  return parseBadgeName(name).some((s) => s.kind === 'letters');
}

/**
 * Spell `text` in badge letters. ASCII letters convert (case folds: indicators
 * have no lowercase); everything else — digits, spaces, punctuation, emoji —
 * passes through untouched. Idempotent: already-badged letters re-encode the same.
 */
export function toBadgeLetters(text: string): string {
  let out = '';
  // Any indicator just emitted — a letter or the tail of a flag — would pair with
  // the next one, so the next indicator is fenced off from it.
  let prevIndicator = false;
  const emit = (indicators: string) => {
    if (prevIndicator) out += BADGE_SEPARATOR;
    out += indicators;
    prevIndicator = true;
  };
  const letter = (upper: string) => String.fromCodePoint(RI_FIRST + upper.charCodeAt(0) - 0x41);

  for (const seg of parseBadgeName(text)) {
    if (seg.kind === 'letters') {
      for (const ch of seg.text) emit(letter(ch));
      continue;
    }
    // Text may hold real flags; keep each pair whole.
    for (const [, flag, ch] of seg.text.matchAll(/([\u{1F1E6}-\u{1F1FF}]{2})|([\s\S])/gu)) {
      if (flag) {
        emit(flag);
        continue;
      }
      const upper = ch.toUpperCase();
      if (ch.length === 1 && upper >= 'A' && upper <= 'Z') {
        emit(letter(upper));
      } else {
        out += ch;
        prevIndicator = false;
      }
    }
  }
  return out;
}

const endsWithIndicator = (s: string) => {
  const last = Array.from(s).pop();
  return last != null && isIndicator(last.codePointAt(0)!);
};

/**
 * Badge `text[start, end)` only — the editor's "convert the selection". The new
 * letters are fenced from any indicator already touching either edge, or the last
 * letter before the selection would pair with the first one in it. An empty range
 * converts the whole text.
 */
export function toBadgeLettersInRange(text: string, start: number, end: number): string {
  if (start >= end) return toBadgeLetters(text);
  const before = text.slice(0, start);
  const after = text.slice(end);
  const mid = toBadgeLetters(text.slice(start, end));
  const fenceLeft = endsWithIndicator(before) && isIndicator(mid.codePointAt(0) ?? 0);
  const fenceRight = endsWithIndicator(mid) && isIndicator(after.codePointAt(0) ?? 0);
  return (
    before + (fenceLeft ? BADGE_SEPARATOR : '') + mid + (fenceRight ? BADGE_SEPARATOR : '') + after
  );
}

/**
 * The first word, as Clubhouse tiles show it. Splits on real whitespace only — a
 * zero-width fence is not a word break, or a badge name would show one letter.
 */
export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] ?? '';
}

/** Up to two initials, read off the plain name so a surrogate pair is never halved. */
export function nameInitials(name: string): string {
  return plainName(name)
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => Array.from(w)[0])
    .join('');
}
