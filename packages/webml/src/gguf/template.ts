/**
 * Chat templates: a GGUF's `tokenizer.chat_template` (Jinja) rendered with
 * `@huggingface/jinja`, the engine transformers.js renders the same templates
 * with, and with the same variables — `messages`, `tools`,
 * `add_generation_prompt`, the special-token strings, and any extra template
 * kwargs (`enable_thinking`, …).
 *
 * The rendered prompt is then tokenized with special-token parsing on and no
 * added BOS, as transformers.js does: a template that wants a BOS writes
 * `{{ bos_token }}` itself.
 */
import { Template } from '@huggingface/jinja';

import type { ChatMessage } from '../protocol';
import type { Tokenizer } from './tokenizer';

/** ChatML: used only for a model with no template whose vocabulary has ChatML markers. */
const CHATML =
  "{% for message in messages %}{{ '<|im_start|>' + message['role'] + '\\n' + message['content'] + '<|im_end|>' + '\\n' }}{% endfor %}" +
  "{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}{% endif %}";

export class ChatTemplate {
  private readonly template: Template;
  private readonly specials: Record<string, string>;

  /** Throws when the model has no template and no ChatML markers to fall back on. */
  constructor(
    source: string | undefined,
    private readonly tokenizer: Tokenizer,
  ) {
    let text = source;
    if (!text) {
      if (
        tokenizer.tokenId('<|im_start|>') === undefined ||
        tokenizer.tokenId('<|im_end|>') === undefined
      ) {
        throw new Error('the model has no chat template (tokenizer.chat_template)');
      }
      text = CHATML;
    }
    this.template = new Template(text);
    const name = (id: number | null) => (id === null ? '' : tokenizer.tokens[id]);
    this.specials = { bos_token: name(tokenizer.bos), eos_token: name(tokenizer.eos) };
  }

  render(
    messages: ChatMessage[],
    options: {
      tools?: unknown[];
      addGenerationPrompt?: boolean;
      kwargs?: Record<string, unknown>;
    } = {},
  ): string {
    try {
      return this.template.render({
        ...this.specials,
        messages,
        tools: options.tools?.length ? options.tools : undefined,
        add_generation_prompt: options.addGenerationPrompt ?? true,
        ...(options.kwargs ?? {}),
      });
    } catch (err) {
      // Includes the template's own raise_exception(...), which the engine defines.
      throw new Error(`chat template: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** The prompt's token ids: rendered, then tokenized with special-token parsing. */
  encode(messages: ChatMessage[], options: Parameters<ChatTemplate['render']>[1] = {}): number[] {
    return this.tokenizer.encode(this.render(messages, options), {
      addBos: false,
      parseSpecial: true,
    });
  }
}
