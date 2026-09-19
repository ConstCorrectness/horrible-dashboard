/**
 * Chat text rules on the client — the same numbers as
 * `backend/modules/hassault/chat.py`, so the counter under the box agrees with
 * what the server will accept. The server still decides; this only keeps the box
 * from letting you type a message that will be cut.
 *
 * Counted in **grapheme clusters** (`Intl.Segmenter`), because that is what a
 * person counts: 👨‍👩‍👧‍👦 is one character to them, eleven UTF-16 units to
 * `String.length`, and an `<input maxLength>` — which counts UTF-16 units — would
 * cut it in the middle and leave a stray emoji fragment behind.
 */

export const MAX_GRAPHEMES = 200;
export const MAX_BYTES = 1024;

const segmenter: Intl.Segmenter | null =
  typeof Intl !== 'undefined' && 'Segmenter' in Intl
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    : null;

/** Split into user-perceived characters. Falls back to code points. */
export function graphemes(text: string): string[] {
  if (segmenter) return Array.from(segmenter.segment(text), (s) => s.segment);
  return Array.from(text);
}

export function graphemeCount(text: string): number {
  return graphemes(text).length;
}

const encoder = new TextEncoder();

/**
 * Trim to the limits at a cluster boundary — never inside an emoji, a flag, or
 * a letter and its accent. Returns the input unchanged when it fits.
 */
export function clampChat(text: string): string {
  const parts = graphemes(text);
  let bytes = 0;
  let kept = 0;
  for (const part of parts) {
    const size = encoder.encode(part).length;
    if (kept >= MAX_GRAPHEMES || bytes + size > MAX_BYTES) break;
    bytes += size;
    kept += 1;
  }
  return kept === parts.length ? text : parts.slice(0, kept).join('');
}

/**
 * The font stack for chat: UI text first, then every platform's colour emoji
 * font by name. Listing them explicitly matters — without them some platforms
 * pick a monochrome emoji glyph out of the text font.
 */
export const CHAT_FONT =
  'Inter, "Segoe UI", system-ui, -apple-system, "Noto Sans", sans-serif, ' +
  '"Segoe UI Emoji", "Segoe UI Symbol", "Apple Color Emoji", "Noto Color Emoji"';
