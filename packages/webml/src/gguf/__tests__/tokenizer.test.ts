/**
 * The tokenizer against llama.cpp's tokenizer test vectors: for each vocab-only
 * GGUF, the ids the Hugging Face tokenizer gives each test string (the `.out`
 * files llama.cpp itself is tested against). The strings cover whitespace runs,
 * digits, apostrophes, CJK, emoji ZWJ sequences and Khmer.
 */
import { describe, expect, it } from 'vitest';

import { readGgufHeader } from '../parse';
import { bytesSource } from '../source';
import { Tokenizer, TokenizerError } from '../tokenizer';
import { VOCABS, vocabCases, vocabFile, type VocabName } from './vocab';

const tokenizers = new Map<VocabName, Promise<Tokenizer>>();
function tokenizer(name: VocabName): Promise<Tokenizer> {
  let t = tokenizers.get(name);
  if (!t) {
    t = vocabFile(`ggml-vocab-${name}.gguf`)
      .then((bytes) => readGgufHeader(bytesSource(bytes)))
      .then((h) => new Tokenizer(h.metadata));
    tokenizers.set(name, t);
  }
  return t;
}

describe.each(VOCABS)('%s', (name) => {
  it('encodes every test string to the reference ids', { timeout: 60_000 }, async () => {
    const tok = await tokenizer(name);
    const failures: string[] = [];
    for (const { text, ids } of await vocabCases(name)) {
      const got = tok.encode(text, { addBos: false });
      if (got.join(' ') !== ids.join(' ')) {
        failures.push(
          `${JSON.stringify(text)}\n   want ${ids.join(' ')}\n    got ${got.join(' ')}`,
        );
      }
    }
    expect(failures, failures.join('\n')).toEqual([]);
  });

  it('decodes what it encodes', { timeout: 60_000 }, async () => {
    const tok = await tokenizer(name);
    for (const { text } of await vocabCases(name)) {
      const decoded = tok.decode(tok.encode(text, { addBos: false }));
      // SentencePiece prepends a space to the first piece.
      expect(name === 'llama-spm' ? decoded.replace(/^ /, '') : decoded).toBe(text);
    }
  });
});

describe('special tokens', () => {
  it('matches control tokens in the text as single ids, longest first', async () => {
    const tok = await tokenizer('qwen2');
    const start = tok.tokenId('<|im_start|>')!;
    const end = tok.tokenId('<|im_end|>')!;
    const ids = tok.encode('<|im_start|>user\nhi<|im_end|>');
    expect(ids[0]).toBe(start);
    expect(ids.at(-1)).toBe(end);
    expect(tok.decode(ids)).toBe('user\nhi');
    expect(tok.decode(ids, true)).toBe('<|im_start|>user\nhi<|im_end|>');
    // Without parseSpecial the markup is ordinary text.
    expect(tok.encode('<|im_end|>', { parseSpecial: false }).length).toBeGreaterThan(1);
  });

  it('ends generation on the eos id and on llama.cpp end-of-turn names', async () => {
    const qwen = await tokenizer('qwen2');
    expect(qwen.eog.has(qwen.tokenId('<|im_end|>')!)).toBe(true);
    expect(qwen.eog.has(qwen.tokenId('<|endoftext|>')!)).toBe(true);
    const llama = await tokenizer('llama-bpe');
    expect(llama.eog.has(llama.tokenId('<|eot_id|>')!)).toBe(true);
  });

  it('adds BOS only when asked and when the model wants it', async () => {
    const llama = await tokenizer('llama-bpe');
    expect(llama.encode('a', { addBos: true })[0]).toBe(llama.bos);
    const qwen = await tokenizer('qwen2');
    expect(qwen.encode('a', { addBos: true })).toEqual(qwen.encode('a'));
  });
});

describe('refusals', () => {
  it('names an unsupported tokenizer model or pre-tokenizer', async () => {
    const h = await readGgufHeader(bytesSource(await vocabFile('ggml-vocab-qwen2.gguf')));
    expect(() => new Tokenizer({ ...h.metadata, 'tokenizer.ggml.pre': 'tekken' })).toThrow(
      /pre-tokenizer "tekken" is not supported/,
    );
    expect(() => new Tokenizer({ ...h.metadata, 'tokenizer.ggml.model': 't5' })).toThrow(
      TokenizerError,
    );
  });
});
