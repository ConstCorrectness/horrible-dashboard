import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { nodeGgufModelId } from '@horrible/webml';

import { registry } from '../../../registry';
import { nodeGgufUrl } from '../engine';
import { canRunNodeGguf, webmlModule } from '../index';
import { playground, splitThinking } from '../playground-state';

beforeEach(() => registry.resetForTests());
afterEach(() => registry.resetForTests());

describe('webml manifest', () => {
  it('registers the playground and its commands', () => {
    registry.register(webmlModule);
    expect((webmlModule.panels ?? []).map((p) => p.id)).toEqual(['webml.playground']);
    expect((webmlModule.commands ?? []).map((c) => c.id)).toEqual([
      'webml.openPlayground',
      'webml.unload',
    ]);
    expect((webmlModule.settings ?? []).map((s) => s.key)).toEqual([
      'webml.defaultModel',
      'webml.temperature',
      'webml.topk',
      'webml.lens',
      'webml.thinking',
    ]);
  });
});

describe('splitThinking', () => {
  it('separates a leading think block from the answer', () => {
    expect(splitThinking('<think>\nhmm\n</think>\n\nHello')).toEqual({
      thinking: 'hmm',
      answer: 'Hello',
    });
    expect(splitThinking('<think>still going')).toEqual({ thinking: 'still going', answer: '' });
    expect(splitThinking('Plain answer')).toEqual({ thinking: null, answer: 'Plain answer' });
    // Only a *leading* block is reasoning; one quoted mid-answer is content.
    expect(splitThinking('Use <think> tags')).toEqual({
      thinking: null,
      answer: 'Use <think> tags',
    });
  });
});

describe('playground transcript', () => {
  it('streams into the assistant turn it began', () => {
    playground.clear();
    const i = playground.begin('hi');
    playground.patch(i, (m) => ({ ...m, content: m.content + 'Hel' }));
    playground.patch(i, (m) => ({ ...m, content: m.content + 'lo' }));
    expect(playground.get().streaming).toBe(i);
    playground.end();
    expect(playground.get()).toEqual({
      messages: [
        { role: 'user', content: 'hi' },
        { role: 'assistant', content: 'Hello', steps: [] },
      ],
      streaming: -1,
    });
    playground.clear();
  });
});

describe('node GGUFs', () => {
  it('resolve to the node catalog file route, the path escaped', () => {
    const path = 'C:/models/trained/my fine-tune.gguf';
    expect(nodeGgufUrl(nodeGgufModelId(path))).toBe(
      `/api/llamacpp/models/file?path=${encodeURIComponent(path)}`,
    );
    expect(nodeGgufUrl('gguf:org/repo/model.gguf')).toBeNull();
    expect(nodeGgufUrl('onnx-community/Qwen3-0.6B-ONNX')).toBeNull();
  });

  it('offer "Run in this window" only for architectures the engine runs', () => {
    expect(canRunNodeGguf('qwen3')).toBe(true);
    expect(canRunNodeGguf('llama')).toBe(true);
    expect(canRunNodeGguf('gemma3')).toBe(true);
    expect(canRunNodeGguf('smollm3')).toBe(true);
    expect(canRunNodeGguf('gemma4')).toBe(false);
    expect(canRunNodeGguf('')).toBe(false);
  });
});
