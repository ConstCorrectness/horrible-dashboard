import { describe, expect, it } from 'vitest';

import { roomModelChoices, unslothSpeechModels } from '../AgentSidebar';
import { DEFAULT_VOICE_CONFIG, chatSpeakerLabel, normalizeVoiceConfig } from '../voiceAgent';

describe('normalizeVoiceConfig', () => {
  it('turns nothing, or nonsense, into the defaults', () => {
    expect(normalizeVoiceConfig(undefined)).toEqual(DEFAULT_VOICE_CONFIG);
    expect(normalizeVoiceConfig(null)).toEqual(DEFAULT_VOICE_CONFIG);
    expect(normalizeVoiceConfig('not an object')).toEqual(DEFAULT_VOICE_CONFIG);
    expect(normalizeVoiceConfig([1, 2])).toEqual(DEFAULT_VOICE_CONFIG);
  });

  it('keeps every field a current config carries', () => {
    const saved = {
      ...DEFAULT_VOICE_CONFIG,
      enabled: true,
      posture: 'always' as const,
      model: 'gemma',
      endpointingDelayMs: 900,
      allowBargeIn: false,
      ttsVolume: '+25%',
    };
    expect(normalizeVoiceConfig(saved)).toEqual(saved);
  });

  it('drops the keys of features that no longer exist', () => {
    const out = normalizeVoiceConfig({
      ...DEFAULT_VOICE_CONFIG,
      wakeWords: ['agent'],
      turnEagerness: 'fast',
      thinkingFiller: true,
      silenceTimeoutS: 30,
      interjectAfterS: 4,
      retrieval: 'auto',
      library: 'default',
    });
    for (const dead of [
      'wakeWords',
      'turnEagerness',
      'thinkingFiller',
      'silenceTimeoutS',
      'interjectAfterS',
      'retrieval',
      'library',
    ]) {
      expect(out).not.toHaveProperty(dead);
    }
  });

  it('turns the interject posture into the nearest one that remains', () => {
    expect(normalizeVoiceConfig({ posture: 'interject' }).posture).toBe('conversational');
    expect(normalizeVoiceConfig({ posture: 'who knows' }).posture).toBe('addressed');
  });

  it('reads an old "turn eagerness" preset as the pause it stood for', () => {
    expect(normalizeVoiceConfig({ turnEagerness: 'fast' }).endpointingDelayMs).toBe(400);
    expect(normalizeVoiceConfig({ turnEagerness: 'patient' }).endpointingDelayMs).toBe(1200);
    // An explicit number wins over the preset it was derived from.
    expect(
      normalizeVoiceConfig({ turnEagerness: 'fast', endpointingDelayMs: 1000 }).endpointingDelayMs,
    ).toBe(1000);
  });

  it('clamps numbers and falls back on junk instead of throwing', () => {
    const out = normalizeVoiceConfig({
      temperature: '',
      maxTokens: 'lots',
      memoryTurns: 9999,
      cooldownS: -5,
      endpointingDelayMs: 50,
    });
    expect(out.temperature).toBe(DEFAULT_VOICE_CONFIG.temperature);
    expect(out.maxTokens).toBe(DEFAULT_VOICE_CONFIG.maxTokens);
    expect(out.memoryTurns).toBe(40);
    expect(out.cooldownS).toBe(0);
    expect(out.endpointingDelayMs).toBe(400);
  });

  it('refuses a wrongly typed boolean or string rather than coercing it', () => {
    const out = normalizeVoiceConfig({ enabled: 'yes', speak: 0, persona: 5, model: '  gemma  ' });
    expect(out.enabled).toBe(false);
    expect(out.speak).toBe(true);
    expect(out.persona).toBe(DEFAULT_VOICE_CONFIG.persona);
    expect(out.model).toBe('gemma');
  });
});

describe('chatSpeakerLabel', () => {
  it('names a chat sender with their handle', () => {
    expect(chatSpeakerLabel({ userName: 'Ada Lovelace', username: 'ada' })).toBe(
      'Ada Lovelace (@ada)',
    );
  });

  it('does not double an @ the payload already carries', () => {
    expect(chatSpeakerLabel({ userName: 'Ada', username: '@ada' })).toBe('Ada (@ada)');
  });

  it('falls back to the handle, then the name', () => {
    expect(chatSpeakerLabel({ username: 'ada' })).toBe('@ada');
    expect(chatSpeakerLabel({ userName: 'Ada' })).toBe('Ada');
  });

  it("treats the pane's placeholders as no sender at all", () => {
    expect(chatSpeakerLabel({ userName: 'Anonymous' })).toBeUndefined();
    expect(chatSpeakerLabel({ userName: 'Unknown', username: null })).toBeUndefined();
  });
});

describe('roomModelChoices', () => {
  const engine = {
    provider: 'nim',
    model: 'google/gemma-4-31b-it',
    available_models: ['google/gemma-4-31b-it'],
    providers: [
      { kind: 'ollama', label: 'Ollama', reachable: false, models: [] },
      { kind: 'lmstudio', label: 'LM Studio', reachable: true, models: ['gemma-3-4b-it'] },
      {
        kind: 'unsloth',
        label: 'Unsloth',
        reachable: true,
        models: [
          'audio-cpp/audio.cpp-gguf/PocketTTS-GGUF',
          'unsloth/Qwen3-4B',
          'openai/whisper-tiny.en',
        ],
      },
      { kind: 'nim', label: 'NVIDIA NIM', reachable: true, models: ['google/gemma-4-31b-it'] },
    ],
  };

  it('lists LLMs from Unsloth, LM Studio, and other connected providers while separating speech models', () => {
    const out = roomModelChoices(engine, '');
    expect(out.groups.map((g) => g.kind)).toEqual(['lmstudio', 'unsloth', 'nim']);
    expect(out.groups[0].options).toEqual([
      { value: 'lmstudio::gemma-3-4b-it', label: 'gemma-3-4b-it' },
    ]);
    expect(out.groups[1].options).toEqual([
      { value: 'unsloth::unsloth/Qwen3-4B', label: 'unsloth/Qwen3-4B' },
    ]);
    expect(unslothSpeechModels(engine)).toEqual(['audio-cpp/audio.cpp-gguf/PocketTTS-GGUF']);
    expect(out.selectedValue).toBe('');
    expect(out.known).toBe(true);
    expect(out.displayModel).toBe('google/gemma-4-31b-it');
    expect(out.servedBy).toBe('nim');
  });

  it('falls back to the default LLM if a TTS model was saved in config.model', () => {
    const out = roomModelChoices(engine, 'unsloth::audio-cpp/audio.cpp-gguf/PocketTTS-GGUF');
    expect(out.selectedValue).toBe('');
    expect(out.displayModel).toBe('google/gemma-4-31b-it');
    expect(out.servedBy).toBe('nim');
  });

  it('resolves a provider::model choice to that provider and model', () => {
    const out = roomModelChoices(engine, 'unsloth::unsloth/Qwen3-4B');
    expect(out.selectedValue).toBe('unsloth::unsloth/Qwen3-4B');
    expect(out.known).toBe(true);
    expect(out.displayModel).toBe('unsloth/Qwen3-4B');
    expect(out.servedBy).toBe('unsloth');
  });

  it('preserves a saved provider::model choice even while that provider is offline', () => {
    const out = roomModelChoices(engine, 'ollama::gemma4:e2b');
    expect(out.selectedValue).toBe('ollama::gemma4:e2b');
    expect(out.known).toBe(false);
    expect(out.unknownLabel).toBe('gemma4:e2b (ollama)');
    expect(out.displayModel).toBe('gemma4:e2b');
    expect(out.servedBy).toBe('ollama');
  });

  it('matches a legacy bare model id against the provider serving it', () => {
    const out = roomModelChoices(engine, 'gemma-3-4b-it');
    expect(out.selectedValue).toBe('lmstudio::gemma-3-4b-it');
    expect(out.known).toBe(true);
    expect(out.displayModel).toBe('gemma-3-4b-it');
    expect(out.servedBy).toBe('lmstudio');
  });
});
