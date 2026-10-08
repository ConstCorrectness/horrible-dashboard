/**
 * The tokenizer a GGUF carries in its metadata (`tokenizer.ggml.*`), ported from
 * llama.cpp's `llama-vocab.cpp` so a prompt becomes the same ids llama.cpp would
 * give it — the parity checks compare against llama.cpp, and a tokenizer that
 * disagrees by one id on a prompt makes every later logit disagree too.
 *
 * Two models:
 *
 *  - `gpt2` (byte-level BPE): the text is split into words by the pre-tokenizer
 *    regexes named by `tokenizer.ggml.pre`, each word's UTF-8 bytes are mapped to
 *    printable characters (GPT-2's byte encoder), and merges are applied lowest
 *    rank first. Only the pre-tokenizers listed in PRE_TOKENIZERS are accepted —
 *    an unknown one is refused, because a wrong regex silently changes every
 *    prompt.
 *  - `llama` (SentencePiece): spaces become ▁, a space is prepended, and adjacent
 *    pieces merge highest score first; anything left without a token falls back to
 *    `<0xXX>` byte tokens.
 *
 * Special tokens (control, user-defined) are matched literally in the text first,
 * longest first, so a chat template's `<|im_start|>` is one token, not seven.
 */
import type { GgufValue } from './parse';

/** `tokenizer.ggml.token_type` values. */
export const TOKEN_NORMAL = 1;
export const TOKEN_UNKNOWN = 2;
export const TOKEN_CONTROL = 3;
export const TOKEN_USER_DEFINED = 4;
export const TOKEN_UNUSED = 5;
export const TOKEN_BYTE = 6;

/**
 * Pre-tokenizer regexes by `tokenizer.ggml.pre`, applied in order (each splits the
 * pieces the previous left). Copied from llama.cpp's `llm_tokenizer_bpe`.
 */
const GPT2 = "'s|'t|'re|'ve|'m|'ll|'d| ?\\p{L}+| ?\\p{N}+| ?[^\\s\\p{L}\\p{N}]+|\\s+(?!\\S)";
const LLAMA3 =
  "(?:'[sS]|'[tT]|'[rR][eE]|'[vV][eE]|'[mM]|'[lL][lL]|'[dD])|[^\\r\\n\\p{L}\\p{N}]?\\p{L}+|\\p{N}{1,3}| ?[^\\s\\p{L}\\p{N}]+[\\r\\n]*|\\s*[\\r\\n]+|\\s+(?!\\S)|\\s+";
const QWEN2 =
  "(?:'[sS]|'[tT]|'[rR][eE]|'[vV][eE]|'[mM]|'[lL][lL]|'[dD])|[^\\r\\n\\p{L}\\p{N}]?\\p{L}+|\\p{N}| ?[^\\s\\p{L}\\p{N}]+[\\r\\n]*|\\s*[\\r\\n]+|\\s+(?!\\S)|\\s+";

interface PreTokenizer {
  regexes: string[];
  /** A word that is itself a token is emitted whole, skipping the merges. */
  ignoreMerges: boolean;
}

const PRE_TOKENIZERS: Record<string, PreTokenizer> = {
  default: {
    regexes: ['[\\p{P}\\$\\+<=>\\^~\\|]+', GPT2, '\\p{N}+', '[0-9][0-9][0-9]'],
    ignoreMerges: false,
  },
  'gpt-2': { regexes: [GPT2], ignoreMerges: false },
  starcoder: { regexes: ['\\p{N}', GPT2], ignoreMerges: false },
  smollm: { regexes: ['\\p{N}', GPT2], ignoreMerges: false },
  'llama-bpe': { regexes: [LLAMA3], ignoreMerges: true },
  llama3: { regexes: [LLAMA3], ignoreMerges: true },
  qwen2: { regexes: [QWEN2], ignoreMerges: false },
  // DeepSeek-R1's Qwen distills: llama.cpp maps it to Qwen2's pre-tokenizer.
  'deepseek-r1-qwen': { regexes: [QWEN2], ignoreMerges: false },
};

export const SUPPORTED_PRE_TOKENIZERS = Object.keys(PRE_TOKENIZERS);

/** Tokens llama.cpp treats as end-of-generation by name (besides the eos/eot/eom ids). */
const EOG_NAMES = new Set([
  '<|eot_id|>',
  '<|im_end|>',
  '<|end|>',
  '<end_of_turn>',
  '<|endoftext|>',
  '<|eom_id|>',
  '<EOT>',
  '<|end_of_text|>',
]);

export class TokenizerError extends Error {
  override name = 'TokenizerError';
}

/** GPT-2's byte ↔ printable-character table. */
function byteTables(): { encode: string[]; decode: Map<string, number> } {
  const bs: number[] = [];
  for (let b = 33; b <= 126; b++) bs.push(b);
  for (let b = 161; b <= 172; b++) bs.push(b);
  for (let b = 174; b <= 255; b++) bs.push(b);
  const encode: string[] = new Array(256);
  for (const b of bs) encode[b] = String.fromCodePoint(b);
  let n = 0;
  for (let b = 0; b < 256; b++) if (!bs.includes(b)) encode[b] = String.fromCodePoint(256 + n++);
  const decode = new Map(encode.map((c, b) => [c, b]));
  return { encode, decode };
}

/** A binary min-heap over `less`. */
class Heap<T> {
  private items: T[] = [];
  constructor(private readonly less: (a: T, b: T) => boolean) {}
  get size(): number {
    return this.items.length;
  }
  push(item: T): void {
    const a = this.items;
    a.push(item);
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (!this.less(a[i], a[p])) break;
      [a[i], a[p]] = [a[p], a[i]];
      i = p;
    }
  }
  pop(): T {
    const a = this.items;
    const top = a[0];
    const last = a.pop()!;
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && this.less(a[l], a[m])) m = l;
        if (r < a.length && this.less(a[r], a[m])) m = r;
        if (m === i) break;
        [a[i], a[m]] = [a[m], a[i]];
        i = m;
      }
    }
    return top;
  }
}

/** A symbol in the merge chain: `text`, linked to neighbours by index; '' once merged away. */
interface Sym {
  text: string;
  prev: number;
  next: number;
}

function chain(parts: string[]): Sym[] {
  return parts.map((text, i) => ({ text, prev: i - 1, next: i + 1 < parts.length ? i + 1 : -1 }));
}

export interface EncodeOptions {
  /** Prepend BOS if the model asks for it (`tokenizer.ggml.add_bos_token`). Default false. */
  addBos?: boolean;
  /** Match control tokens written in the text (chat templates do). Default true. */
  parseSpecial?: boolean;
}

export class Tokenizer {
  readonly model: 'gpt2' | 'llama';
  readonly pre: string;
  readonly tokens: string[];
  readonly types: Int32Array;
  readonly bos: number | null;
  readonly eos: number | null;
  readonly addBos: boolean;
  /** Ids that end a generation. */
  readonly eog: ReadonlySet<number>;

  private readonly ids = new Map<string, number>();
  private readonly ranks = new Map<string, number>();
  private readonly scores: Float32Array | null;
  private readonly preTokenizer: PreTokenizer | null;
  private readonly regexes: RegExp[];
  private readonly addSpacePrefix: boolean;
  /** Special tokens, longest text first, as llama.cpp partitions them. */
  private readonly specials: number[];
  private readonly bytes = byteTables();
  private readonly utf8 = new TextEncoder();

  constructor(meta: Record<string, GgufValue>) {
    const model = meta['tokenizer.ggml.model'];
    if (model !== 'gpt2' && model !== 'llama') {
      throw new TokenizerError(
        `tokenizer "${String(model)}" is not supported (supported: gpt2, llama)`,
      );
    }
    this.model = model;
    const tokens = meta['tokenizer.ggml.tokens'];
    if (!Array.isArray(tokens) || !tokens.every((t) => typeof t === 'string')) {
      throw new TokenizerError('tokenizer.ggml.tokens is missing');
    }
    this.tokens = tokens as string[];
    tokens.forEach((t, i) => {
      // The first id wins for a duplicate text, as in llama.cpp's token_to_id.
      if (!this.ids.has(t as string)) this.ids.set(t as string, i);
    });
    const types = meta['tokenizer.ggml.token_type'];
    this.types =
      types instanceof Int32Array ? types : new Int32Array(tokens.length).fill(TOKEN_NORMAL);

    const id = (key: string): number | null => {
      const v = meta[key];
      return typeof v === 'number' && v >= 0 && v < tokens.length ? v : null;
    };
    this.bos = id('tokenizer.ggml.bos_token_id');
    this.eos = id('tokenizer.ggml.eos_token_id');
    const flag = (key: string, fallback: boolean) =>
      typeof meta[key] === 'boolean' ? (meta[key] as boolean) : fallback;

    if (model === 'gpt2') {
      const pre =
        typeof meta['tokenizer.ggml.pre'] === 'string' ? meta['tokenizer.ggml.pre'] : 'default';
      this.pre = pre;
      const p = PRE_TOKENIZERS[pre];
      if (!p) {
        throw new TokenizerError(
          `pre-tokenizer "${pre}" is not supported (supported: ${SUPPORTED_PRE_TOKENIZERS.join(', ')})`,
        );
      }
      this.preTokenizer = p;
      this.regexes = p.regexes.map((r) => new RegExp(r, 'gu'));
      const merges = meta['tokenizer.ggml.merges'];
      if (!Array.isArray(merges)) throw new TokenizerError('tokenizer.ggml.merges is missing');
      merges.forEach((m, rank) => {
        if (typeof m === 'string' && !this.ranks.has(m)) this.ranks.set(m, rank);
      });
      this.scores = null;
      this.addSpacePrefix = false;
      this.addBos = flag('tokenizer.ggml.add_bos_token', pre === 'llama-bpe' || pre === 'llama3');
    } else {
      this.pre = '';
      this.preTokenizer = null;
      this.regexes = [];
      const scores = meta['tokenizer.ggml.scores'];
      if (!(scores instanceof Float32Array))
        throw new TokenizerError('tokenizer.ggml.scores is missing');
      this.scores = scores;
      this.addSpacePrefix = flag('tokenizer.ggml.add_space_prefix', true);
      this.addBos = flag('tokenizer.ggml.add_bos_token', true);
    }

    this.specials = [];
    for (let i = 0; i < tokens.length; i++) {
      const t = this.types[i];
      if (t === TOKEN_CONTROL || t === TOKEN_USER_DEFINED || t === TOKEN_UNKNOWN)
        this.specials.push(i);
    }
    this.specials.sort((a, b) => this.tokens[b].length - this.tokens[a].length);

    const eog = new Set<number>();
    for (const key of ['eos', 'eot', 'eom']) {
      const v = id(`tokenizer.ggml.${key}_token_id`);
      if (v !== null) eog.add(v);
    }
    for (const name of EOG_NAMES) {
      const v = this.ids.get(name);
      if (v !== undefined) eog.add(v);
    }
    this.eog = eog;
  }

  get vocabSize(): number {
    return this.tokens.length;
  }

  /** The id of a token's exact text, if the vocabulary has it. */
  tokenId(text: string): number | undefined {
    return this.ids.get(text);
  }

  encode(text: string, options: EncodeOptions = {}): number[] {
    const out: number[] = [];
    if (options.addBos && this.addBos && this.bos !== null) out.push(this.bos);
    let prevSpecial = true;
    for (const frag of this.partition(text, options.parseSpecial ?? true)) {
      if (typeof frag === 'number') {
        out.push(frag);
        prevSpecial = true;
        continue;
      }
      if (this.model === 'gpt2') this.encodeBpe(frag, out);
      else this.encodeSpm((this.addSpacePrefix && prevSpecial ? ' ' : '') + frag, out);
      prevSpecial = false;
    }
    return out;
  }

  /** Text and special-token ids, in order. Specials are split out longest first. */
  private partition(text: string, parseSpecial: boolean): (string | number)[] {
    let frags: (string | number)[] = text ? [text] : [];
    for (const id of this.specials) {
      const t = this.types[id];
      if (!parseSpecial && (t === TOKEN_CONTROL || t === TOKEN_UNKNOWN)) continue;
      const needle = this.tokens[id];
      if (!needle) continue;
      const next: (string | number)[] = [];
      for (const f of frags) {
        if (typeof f === 'number' || !f.includes(needle)) {
          next.push(f);
          continue;
        }
        const parts = f.split(needle);
        parts.forEach((p, i) => {
          if (i > 0) next.push(id);
          if (p) next.push(p);
        });
      }
      frags = next;
    }
    return frags;
  }

  /** Words by the pre-tokenizer: each regex splits every piece, keeping the gaps. */
  private words(text: string): string[] {
    let pieces = [text];
    for (const re of this.regexes) {
      const next: string[] = [];
      for (const piece of pieces) {
        let at = 0;
        for (const m of piece.matchAll(re)) {
          if (m.index > at) next.push(piece.slice(at, m.index));
          if (m[0]) next.push(m[0]);
          at = m.index + m[0].length;
        }
        if (at < piece.length) next.push(piece.slice(at));
      }
      pieces = next;
    }
    return pieces;
  }

  private encodeBpe(text: string, out: number[]): void {
    for (const raw of this.words(text)) {
      let word = '';
      for (const b of this.utf8.encode(raw)) word += this.bytes.encode[b];
      if (this.preTokenizer!.ignoreMerges) {
        const whole = this.ids.get(word);
        if (whole !== undefined) {
          out.push(whole);
          continue;
        }
      }
      const syms = chain(Array.from(word));
      type Bigram = { left: number; right: number; text: string; rank: number };
      const queue = new Heap<Bigram>(
        (a, b) => a.rank < b.rank || (a.rank === b.rank && a.left < b.left),
      );
      const add = (left: number, right: number) => {
        if (left < 0 || right < 0) return;
        const rank = this.ranks.get(`${syms[left].text} ${syms[right].text}`);
        if (rank !== undefined) {
          queue.push({ left, right, text: syms[left].text + syms[right].text, rank });
        }
      };
      for (let i = 1; i < syms.length; i++) add(i - 1, i);
      while (queue.size) {
        const bg = queue.pop();
        const l = syms[bg.left];
        const r = syms[bg.right];
        if (!l.text || !r.text || l.text + r.text !== bg.text) continue;
        l.text += r.text;
        r.text = '';
        l.next = r.next;
        if (r.next >= 0) syms[r.next].prev = bg.left;
        add(l.prev, bg.left);
        add(bg.left, l.next);
      }
      for (const s of syms) {
        if (!s.text) continue;
        const id = this.ids.get(s.text);
        if (id !== undefined) {
          out.push(id);
          continue;
        }
        // No token for the merged text: one token per byte-character, where they exist.
        for (const c of s.text) {
          const b = this.ids.get(c);
          if (b !== undefined) out.push(b);
        }
      }
    }
  }

  private encodeSpm(text: string, out: number[]): void {
    const syms = chain(Array.from(text.replaceAll(' ', '\u2581')));
    type Bigram = { left: number; right: number; score: number; size: number };
    const queue = new Heap<Bigram>(
      (a, b) => a.score > b.score || (a.score === b.score && a.left < b.left),
    );
    const add = (left: number, right: number) => {
      if (left < 0 || right < 0) return;
      const t = syms[left].text + syms[right].text;
      const id = this.ids.get(t);
      if (id !== undefined) queue.push({ left, right, score: this.scores![id], size: t.length });
    };
    for (let i = 1; i < syms.length; i++) add(i - 1, i);
    while (queue.size) {
      const bg = queue.pop();
      const l = syms[bg.left];
      const r = syms[bg.right];
      if (!l.text || !r.text || l.text.length + r.text.length !== bg.size) continue;
      l.text += r.text;
      r.text = '';
      l.next = r.next;
      if (r.next >= 0) syms[r.next].prev = bg.left;
      add(l.prev, bg.left);
      add(bg.left, l.next);
    }
    // A merge only happens when the merged text is a token, so a symbol is either a
    // token or one unmerged character; the latter falls back to <0xXX> byte tokens.
    // (llama.cpp's `resegment` also consults a reverse-merge map, which for that
    // reason never matches a final symbol.)
    for (const s of syms) {
      if (!s.text) continue;
      const id = this.ids.get(s.text);
      if (id !== undefined) {
        out.push(id);
        continue;
      }
      for (const b of this.utf8.encode(s.text)) {
        const byte = this.ids.get(`<0x${b.toString(16).toUpperCase().padStart(2, '0')}>`);
        if (byte !== undefined) out.push(byte);
      }
    }
  }

  /**
   * The bytes a token stands for. Control and unknown tokens are empty unless
   * `special` (as in llama.cpp's token_to_piece).
   */
  piece(id: number, special = false): Uint8Array {
    const text = this.tokens[id];
    const type = this.types[id];
    if (text === undefined) return new Uint8Array(0);
    if (type === TOKEN_CONTROL || type === TOKEN_UNKNOWN) {
      return special ? this.utf8.encode(text) : new Uint8Array(0);
    }
    if (type === TOKEN_USER_DEFINED) return this.utf8.encode(text);
    if (type === TOKEN_BYTE) {
      const m = /^<0x([0-9A-Fa-f]{2})>$/.exec(text);
      return m ? new Uint8Array([parseInt(m[1], 16)]) : new Uint8Array(0);
    }
    if (this.model === 'llama') return this.utf8.encode(text.replaceAll('▁', ' '));
    const out = new Uint8Array(text.length * 3);
    let n = 0;
    for (const c of text) {
      const b = this.bytes.decode.get(c);
      if (b !== undefined) out[n++] = b;
      else for (const x of this.utf8.encode(c)) out[n++] = x;
    }
    return out.subarray(0, n);
  }

  /** Text for a run of ids. */
  decode(ids: number[], special = false): string {
    const parts = ids.map((id) => this.piece(id, special));
    const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of parts) {
      all.set(p, at);
      at += p.length;
    }
    // A token can begin with U+FEFF (see parse.ts); keep it, as llama.cpp does.
    return new TextDecoder('utf-8', { ignoreBOM: true }).decode(all);
  }
}
