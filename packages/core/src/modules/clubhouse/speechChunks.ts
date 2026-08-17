/**
 * Splitting a spoken reply into chunks that can be synthesized and played
 * independently.
 *
 * The agent used to synthesize a whole reply, transfer it, then decode it, before a
 * single sound reached the room. That cost is paid in full no matter how fast the
 * model is: measured against the current Edge TTS backend, a 69-word reply took
 * ~690 ms warm (and ~2.5 s cold) before playback began, while the synthesizer's
 * *first* audio was ready in ~275 ms and stayed flat regardless of length.
 *
 * So the win is not a smaller model, it is not waiting for the tail. Chunks are
 * synthesized ahead while the previous one plays, and the first chunk is deliberately
 * the smallest — it is the only one whose latency the listener actually experiences.
 */

/** Longest chunk we will ask for after the first. Long enough to keep prosody
 *  natural across a sentence, short enough that one slow chunk cannot stall the
 *  queue for seconds. */
const MAX_CHUNK_CHARS = 240;

/** Below this a chunk is merged forward instead of being spoken alone: "Right." as
 *  its own request is a whole round trip for a syllable, and the pause it leaves
 *  reads as the agent hesitating. */
const MIN_CHUNK_CHARS = 24;

/**
 * Sentence-ish boundaries: `.`/`!`/`?` (plus closing quotes/brackets) followed by
 * whitespace. Deliberately not a full sentence tokenizer — the cost of a wrong split
 * is one slightly odd pause, and the abbreviations that would fool it ("Dr.", "e.g.")
 * are rare in speech the model writes to be read aloud.
 */
const SENTENCE_END = /([.!?]+["')\]]*)(\s+)/g;

function sentences(text: string): string[] {
  const out: string[] = [];
  let last = 0;
  for (const match of text.matchAll(SENTENCE_END)) {
    const end = match.index! + match[1].length;
    out.push(text.slice(last, end).trim());
    last = end + match[2].length;
  }
  const tail = text.slice(last).trim();
  if (tail) out.push(tail);
  return out.filter(Boolean);
}

/** Break an over-long sentence on a comma or, failing that, a word boundary. */
function hardWrap(sentence: string, limit: number): string[] {
  if (sentence.length <= limit) return [sentence];
  const parts: string[] = [];
  let rest = sentence;
  while (rest.length > limit) {
    const window = rest.slice(0, limit);
    // Prefer a clause boundary; a chunk that ends mid-phrase is audible.
    let cut = Math.max(window.lastIndexOf(', '), window.lastIndexOf('; '));
    if (cut < limit * 0.4) cut = window.lastIndexOf(' ');
    if (cut <= 0) cut = limit;
    parts.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) parts.push(rest);
  return parts;
}

/**
 * Split a reply into speakable chunks, first-chunk-smallest.
 *
 * `firstChunkChars` keeps the opening chunk short so the room hears something as
 * soon as possible; everything after it is allowed to be longer, because by then
 * playback is already running and synthesis is hidden behind it.
 */
export function splitForSpeech(
  text: string,
  { firstChunkChars = 120, maxChunkChars = MAX_CHUNK_CHARS } = {},
): string[] {
  const clean = (text ?? '').trim();
  if (!clean) return [];

  const pieces = sentences(clean).flatMap((s) => hardWrap(s, maxChunkChars));
  if (pieces.length === 0) return [];

  const chunks: string[] = [];
  for (const piece of pieces) {
    const limit = chunks.length === 0 ? firstChunkChars : maxChunkChars;
    const current = chunks[chunks.length - 1];
    // Merge forward while the running chunk is still too short to be worth its own
    // request — but never past the limit for that position.
    if (
      current !== undefined &&
      current.length < MIN_CHUNK_CHARS &&
      current.length + piece.length + 1 <= limit
    ) {
      chunks[chunks.length - 1] = `${current} ${piece}`;
      continue;
    }
    // The first chunk stays alone once it is long enough to be worth speaking, so
    // the opening latency is never inflated by appending the second sentence to it.
    if (chunks.length === 1 && chunks[0].length + piece.length + 1 <= firstChunkChars) {
      chunks[0] = `${chunks[0]} ${piece}`;
      continue;
    }
    chunks.push(piece);
  }
  return chunks;
}
