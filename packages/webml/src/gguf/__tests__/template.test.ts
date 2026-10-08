import { describe, expect, it } from 'vitest';

import type { GgufValue } from '../parse';
import { readGgufHeader } from '../parse';
import { bytesSource } from '../source';
import { ChatTemplate } from '../template';
import { Tokenizer } from '../tokenizer';
import { vocabFile } from './vocab';

async function qwen2(): Promise<{ meta: Record<string, GgufValue>; tok: Tokenizer }> {
  const h = await readGgufHeader(bytesSource(await vocabFile('ggml-vocab-qwen2.gguf')));
  return { meta: h.metadata, tok: new Tokenizer(h.metadata) };
}

describe('ChatTemplate', () => {
  it("renders the model's own template (Qwen2) and tokenizes its markup as specials", async () => {
    const { meta, tok } = await qwen2();
    const t = new ChatTemplate(meta['tokenizer.chat_template'] as string, tok);
    const prompt = t.render([{ role: 'user', content: 'Hi there' }]);
    expect(prompt).toBe(
      '<|im_start|>system\nYou are a helpful assistant<|im_end|>\n' +
        '<|im_start|>user\nHi there<|im_end|>\n' +
        '<|im_start|>assistant\n',
    );
    const ids = t.encode([{ role: 'user', content: 'Hi there' }]);
    expect(ids[0]).toBe(tok.tokenId('<|im_start|>'));
    expect(ids.filter((id) => id === tok.tokenId('<|im_end|>'))).toHaveLength(2);
    expect(tok.decode(ids, true)).toBe(prompt);
  });

  it('falls back to ChatML only when the vocabulary has its markers', async () => {
    const { tok } = await qwen2();
    expect(new ChatTemplate(undefined, tok).render([{ role: 'user', content: 'x' }])).toBe(
      '<|im_start|>user\nx<|im_end|>\n<|im_start|>assistant\n',
    );
    const h = await readGgufHeader(bytesSource(await vocabFile('ggml-vocab-gpt-2.gguf')));
    expect(() => new ChatTemplate(undefined, new Tokenizer(h.metadata))).toThrow(
      /no chat template/,
    );
  });

  it('passes bos_token, tools and extra kwargs through as transformers.js does', async () => {
    const h = await readGgufHeader(bytesSource(await vocabFile('ggml-vocab-llama-bpe.gguf')));
    const tok = new Tokenizer(h.metadata);
    const t = new ChatTemplate(
      '{{ bos_token }}{% if tools %}TOOLS:{{ tools | length }}|{% endif %}' +
        '{% for m in messages %}<|start_header_id|>{{ m.role }}<|end_header_id|>\n\n{{ m.content }}<|eot_id|>{% endfor %}' +
        '{% if enable_thinking is defined and not enable_thinking %}/no_think{% endif %}',
      tok,
    );
    const prompt = t.render([{ role: 'user', content: 'q' }], {
      tools: [{ type: 'function', function: { name: 'f' } }],
      kwargs: { enable_thinking: false },
    });
    expect(prompt).toBe(
      '<|begin_of_text|>TOOLS:1|<|start_header_id|>user<|end_header_id|>\n\nq<|eot_id|>/no_think',
    );
    // The template wrote BOS itself; encoding must not add a second one.
    const ids = t.encode([{ role: 'user', content: 'q' }]);
    expect(ids.filter((id) => id === tok.bos)).toHaveLength(1);
  });

  it("surfaces a template's raise_exception as an error", async () => {
    const { tok } = await qwen2();
    const t = new ChatTemplate("{{ raise_exception('roles must alternate') }}", tok);
    expect(() => t.render([])).toThrow(/chat template: roles must alternate/);
  });
});
