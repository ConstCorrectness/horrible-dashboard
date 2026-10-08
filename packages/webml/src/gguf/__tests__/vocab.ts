/**
 * llama.cpp's vocab-only GGUFs and their tokenizer test files, fetched once into
 * `<repo>/.cache/webml-vocab/` (gitignored) and checked by SHA-256.
 *
 * They are ~18 MB together — too large to commit — and immutable at the pinned
 * release tag: the `.out` files are the Hugging Face tokenizers' ids for the
 * `.inp` strings, which llama.cpp's own tokenizer tests check against too.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const TAG = 'b6800';
const BASE = `https://raw.githubusercontent.com/ggml-org/llama.cpp/${TAG}/models/`;
const CACHE = fileURLToPath(new URL('../../../../../.cache/webml-vocab/', import.meta.url));

const SHA256: Record<string, string> = {
  'ggml-vocab-llama-bpe.gguf': '97272e430d53bc7688f52d5e0ad8ea8f163ede9f1bbd1694feaa504797d5d96e',
  'ggml-vocab-llama-bpe.gguf.inp':
    'be0a11f7071f0c67d3053a2d377d8f35f0dbcb77ff11a60300fb57d36b477cf0',
  'ggml-vocab-llama-bpe.gguf.out':
    '118abf2034a197fc5c9dec5204dfdeee7004c7c70952ae577f66b99e212fab9b',
  'ggml-vocab-qwen2.gguf': '44c2f46b715f585c6ab513970e8a006bfa5badd6108560054921cf598d154d8c',
  'ggml-vocab-qwen2.gguf.inp': 'be0a11f7071f0c67d3053a2d377d8f35f0dbcb77ff11a60300fb57d36b477cf0',
  'ggml-vocab-qwen2.gguf.out': 'de785bb305cc6fa9d43908ed5da337f853c0b79e971348fcfb9f0915a39c5363',
  'ggml-vocab-llama-spm.gguf': '16c3724582d59aa8bf84711894e833f916ee46a31d80e21312759c48bf8d0e69',
  'ggml-vocab-llama-spm.gguf.inp':
    'be0a11f7071f0c67d3053a2d377d8f35f0dbcb77ff11a60300fb57d36b477cf0',
  'ggml-vocab-llama-spm.gguf.out':
    'ad6905c925c49c022974fe67e030382e8a4f56eb4489a0b9481dcedb1ec73b29',
  'ggml-vocab-gpt-2.gguf': 'cedc56ca6e2e89f63e781696d1fd76b4b1d49e6720dee86463e915f6e90016ac',
  'ggml-vocab-gpt-2.gguf.inp': 'be0a11f7071f0c67d3053a2d377d8f35f0dbcb77ff11a60300fb57d36b477cf0',
  'ggml-vocab-gpt-2.gguf.out': '48e8c4cb3ad5c37915b225f872222567c1c4dd4d66951aa53460c8d152ff6d60',
  'ggml-vocab-starcoder.gguf': 'fedb892b4e1bd3c1f2fcdae356440b14fb458f4264d586e5c987ed93df4e174d',
  'ggml-vocab-starcoder.gguf.inp':
    'be0a11f7071f0c67d3053a2d377d8f35f0dbcb77ff11a60300fb57d36b477cf0',
  'ggml-vocab-starcoder.gguf.out':
    '3ef12183c57ad076f9defd8e5eb454f509f5f0865cad35646abb4c4b2d2c5764',
};

export const VOCABS = ['llama-bpe', 'qwen2', 'llama-spm', 'gpt-2', 'starcoder'] as const;
export type VocabName = (typeof VOCABS)[number];

const sha256 = (b: Uint8Array) => createHash('sha256').update(b).digest('hex');

/** The file's bytes, from the cache or downloaded into it. Throws on a hash mismatch. */
export async function vocabFile(name: string): Promise<Uint8Array> {
  const want = SHA256[name];
  if (!want) throw new Error(`unknown vocab file ${name}`);
  const path = CACHE + name;
  if (existsSync(path)) {
    const bytes = new Uint8Array(readFileSync(path));
    if (sha256(bytes) === want) return bytes;
  }
  const res = await fetch(BASE + name);
  if (!res.ok)
    throw new Error(`${BASE + name}: HTTP ${res.status} (tokenizer tests need the network once)`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (sha256(bytes) !== want) throw new Error(`${name}: SHA-256 mismatch from ${BASE}`);
  mkdirSync(CACHE, { recursive: true });
  writeFileSync(path, bytes);
  return bytes;
}

/** The `.inp` strings and `.out` ids of one vocab's tokenizer test. */
export async function vocabCases(name: VocabName): Promise<{ text: string; ids: number[] }[]> {
  const decode = (b: Uint8Array) => new TextDecoder().decode(b);
  const inputs = decode(await vocabFile(`ggml-vocab-${name}.gguf.inp`)).split(
    '\n__ggml_vocab_test__\n',
  );
  const outputs = decode(await vocabFile(`ggml-vocab-${name}.gguf.out`)).split('\n');
  // The .inp file ends with a separator line; drop the empty tail on both sides.
  if (inputs.at(-1)?.trim() === '__ggml_vocab_test__' || inputs.at(-1) === '') inputs.pop();
  return inputs.map((text, i) => ({
    text,
    ids: outputs[i].trim() ? outputs[i].trim().split(/\s+/).map(Number) : [],
  }));
}
